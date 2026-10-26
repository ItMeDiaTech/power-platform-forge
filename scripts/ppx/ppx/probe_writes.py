"""ppx probe --write: the write probes against the scratch items the user names, and their plan."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import partial

from ppx import apis, http, resolve
from ppx.errors import PpxError
from ppx.probe_records import KINDS, ORDER, SLOW, _GUID, _ok, _settle


def _plan_writes(ctx, args, backups: dict[str, str]) -> None:
    where = f"workspace {args.workspace}"
    if args.model:
        ctx.changes.add("create a scratch report bound to the model, then delete it", f"{where}, model {args.model}",
                        undo="the probe deletes the report it creates")
    if args.report:
        ctx.changes.add("write the report's own definition back unchanged (updateDefinition)",
                        f"{where}, report {args.report}",
                        backup=f"<data>/{backups['report']} holds the definition pulled just before")
    if args.model:
        ctx.changes.add("write the model's own definition back unchanged (updateDefinition, allowPurgeData false)",
                        f"{where}, model {args.model}",
                        backup=f"<data>/{backups['model']} holds the definition pulled just before")
        # Not reversible from here: only the previous owner can take it back, and its refresh
        # credentials stay with them, so a wrongly named shared model can stop refreshing.
        ctx.changes.add("take over the model (you become its owner)", f"{where}, model {args.model}",
                        reversible=False, undo="the previous owner can take it over again")


class _WriteProbes:
    """The write half of the probe; mixed into _Probe, whose check, via and _pull it uses."""

    # -- write probes ---------------------------------------------------------------------------

    def writes(self) -> None:
        targets = self.check("write", "targets", self._targets)
        if not targets:
            for name in ("create-report", "updateDefinition-report", "updateDefinition-model", "takeover"):
                self.skip("write", name, "scratch targets not resolved")
            return
        ws, model, report = targets["workspace"], targets.get("model"), targets.get("report")
        scratch = f"ppx-probe-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        jobs = []
        if model:
            # POST, its operation (SLOW spans) and the DELETE all fit in the check's budget.
            jobs.append(("create-report", partial(self._create_report, ws, model, scratch), SLOW + 2))
        jobs += [(f"updateDefinition-{kind}", partial(self._update_definition, kind, ws, item), 2 * SLOW)
                 for kind, item in (("report", report), ("model", model)) if item]
        if model:
            jobs.append(("takeover", partial(self._takeover, ws, model), 1))
        for name, fn, spans in jobs:
            got = self.check("write", name, fn, spans)
            if got:
                key, attempts = got
                self.evidence.setdefault(key, []).extend((a["source"], a["status"]) for a in attempts)
        left = self.results["write"].get("create-report", {})
        if left.get("code") in ("not-deleted", "maybe-created"):
            self.ctx.warn(left["message"])
        elif left.get("status") == "timeout":
            self.ctx.warn(f"creating scratch report {scratch} ran out of time and it may still appear in workspace "
                          f"{ws}; delete it there")

    def _targets(self) -> dict:
        a = self.args
        ws = self.ids.get("workspace") or self._lookup("workspace", a.workspace, "groups")
        out = {"workspace": ws["id"]}
        for kind, wanted, path in (("model", a.model, "datasets"), ("report", a.report, "reports")):
            if wanted:
                out[kind] = (self.ids.get(kind) or self._lookup(kind, wanted, f"groups/{ws['id']}/{path}"))["id"]
        return _ok({"resolved": sorted(out)}, out, value=out)

    def _lookup(self, kind: str, wanted: str, path: str) -> dict:
        if _GUID.fullmatch(wanted.strip()):
            return {"id": wanted.strip()}
        api = apis.pbi()
        attempts = self.via(api.resource, ORDER, lambda prov: http.paged(
            api.url(path), audience=api.resource, timeout=self.timeout, token_provider=prov)[0])
        return resolve.pick(kind, wanted, next((a["value"] for a in attempts if a["status"] == "ok"), []))

    def _write(self, key: str, fn, summarise=lambda value: {}) -> dict:
        attempts = self.via(apis.by_name(key).resource, ORDER, fn, write=True)
        out = _settle(attempts, summarise)
        out["value"] = (key, attempts)
        return out

    def _create_report(self, ws: str, model: str, name: str) -> dict:
        api = apis.fabric()
        body = {"displayName": name,
                "creationPayload": {"semanticModelReference": {"referenceType": "ById", "itemId": model,
                                                               "workspaceId": ws}}}

        def create(prov):
            resp = http.request("POST", api.url(f"workspaces/{ws}/reports"), audience=api.resource, op="write",
                                json_body=body, timeout=self.timeout, token_provider=prov)
            try:
                created = http.wait_operation(resp, audience=api.resource, timeout=self.timeout * SLOW,
                                              token_provider=prov, fetch_result=True) or {}
                if not created.get("id"):
                    raise PpxError("create report answered without the new report's id")
            except PpxError as exc:
                # Accepted but not confirmed: Fabric may still create it, and no other source may try again.
                raise PpxError(f"scratch report {name} may exist in workspace {ws} ({exc.message}); delete it there",
                               data={"code": "maybe-created"}) from exc
            try:  # inside, so a failed delete never makes the next source create a second report
                http.request("DELETE", api.url(f"workspaces/{ws}/reports/{created['id']}"), audience=api.resource,
                             op="write", timeout=self.timeout, token_provider=prov)
            except PpxError as exc:
                return {"created": created, "lro": resp.status == 202, "deleted": False, "error": exc.message}
            return {"created": created, "lro": resp.status == 202, "deleted": True}

        out = self._write("fabric", create, lambda v: {"lro": v["lro"], "deleted": v["deleted"]})
        done = next((a["value"] for a in out["value"][1] if a["status"] == "ok"), None)
        if done and not done["deleted"]:
            out.update(status="error", code="not-deleted",
                       message=f"the probe created scratch report {name} ({done['created']['id']}) but could not "
                               f"delete it ({done['error']}); delete it in workspace {ws}")
        return out

    def _update_definition(self, kind: str, ws: str, item: str) -> dict:
        api = apis.fabric()
        path = f"workspaces/{ws}/{KINDS[kind][0]}/{item}"
        # A report goes back in the format it is stored in (PBIR or PBIR-Legacy): writing the PBIR pull of a
        # PBIR-Legacy report would convert it. A model's TMDL is only a serialisation, so that pull is reused.
        fmt = KINDS[kind][1] if kind == "model" else None
        definition = self.definitions.get(kind)
        if not definition:
            pulled = self.via(api.resource, ORDER, partial(self._pull, path, fmt))
            definition = next((a["value"]["definition"] for a in pulled if a["status"] == "ok"), None)
        if not (definition or {}).get("parts"):
            return {"status": "error", "code": "no-definition", "message": f"could not pull the {kind} definition "
                                                                           "to write back", "raw": {}}
        body = {"definition": definition}
        if kind == "model":
            body["options"] = {"allowPurgeData": False}
        backup = self.backups[kind]
        try:
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({"definition": definition}, indent=1), encoding="utf-8")
        except OSError as exc:
            return {"status": "error", "code": "no-backup", "message": f"not written back: could not save the {kind} "
                                                                       f"definition first ({exc.strerror or exc})",
                    "raw": {}}

        def update(prov):
            resp = http.request("POST", api.url(f"{path}/updateDefinition"), audience=api.resource, op="write",
                                params={"updateMetadata": "false"}, json_body=body, timeout=self.timeout,
                                token_provider=prov)
            http.wait_operation(resp, audience=api.resource, timeout=self.timeout * SLOW, token_provider=prov)
            return {"lro": resp.status == 202}
        return self._write("fabric", update, lambda v: v)

    def _takeover(self, ws: str, model: str) -> dict:
        api = apis.pbi()

        def take(prov):
            http.request("POST", api.url(f"groups/{ws}/datasets/{model}/Default.TakeOver"), audience=api.resource,
                         op="write", body=b"", timeout=self.timeout, token_provider=prov)
            return {}
        return self._write("pbi", take)
