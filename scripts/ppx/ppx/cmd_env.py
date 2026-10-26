"""ppx env: profiles of defaults and the notice mode."""

from __future__ import annotations

from ppx import envs
from ppx.errors import UsageError


def _save(ctx) -> None:
    if not ctx.dry_run:
        envs.save(ctx.need_data_dir(), ctx.state)


def add(ctx, args) -> dict:
    ctx.need_data_dir()
    if args.name in ctx.state.profiles:
        raise UsageError(f"profile {args.name!r} exists; change it with `ppx env set {args.name} ...`")
    profile = envs.Profile(args.name, envs.check_url(args.url, "--url"), args.workspace, envs.check_url(args.site, "--site"))
    ctx.state.profiles[args.name] = profile
    if args.use or ctx.state.current is None:
        ctx.state.current = args.name
    ctx.changes.add(f"save profile {args.name}", "this computer", undo=f"ppx env rm {args.name}")
    _save(ctx)
    return {"profile": profile.to_dict(), "active": ctx.state.current}


def set_(ctx, args) -> dict:
    ctx.need_data_dir()
    name = args.name or ctx.state.current
    changed = []
    if args.notice_mode:
        ctx.state.notice_mode = args.notice_mode
        changed.append(f"notice mode {args.notice_mode}")
    if any(v is not None for v in (args.url, args.workspace, args.site)):
        if not name or name not in ctx.state.profiles:
            raise UsageError(f"no profile {name!r}; create it with `ppx env add`")
        p = ctx.state.profiles[name]
        if args.url is not None:
            p.url = envs.check_url(args.url, "--url")
        if args.workspace is not None:
            p.workspace = args.workspace or None
        if args.site is not None:
            p.site = envs.check_url(args.site, "--site") if args.site else None
        changed.append(f"profile {name}")
    if not changed:
        raise UsageError("nothing to change: pass --url, --workspace, --site or --notice-mode")
    ctx.changes.add("update " + " and ".join(changed), "this computer")
    _save(ctx)
    return {"notice_mode": ctx.state.notice_mode, "profile": ctx.state.profiles[name].to_dict() if name in ctx.state.profiles else None}


def rm(ctx, args) -> dict:
    ctx.need_data_dir()
    if args.name not in ctx.state.profiles:
        raise UsageError(f"no profile {args.name!r}")
    removed = ctx.state.profiles.pop(args.name)
    if ctx.state.current == args.name:
        ctx.state.current = next(iter(ctx.state.profiles), None)
    undo = " ".join(["ppx env add", removed.name] + [f"--{k} {v}" for k, v in removed.to_dict().items() if k != "name"])
    ctx.changes.add(f"delete profile {args.name}", "this computer", undo=undo)
    _save(ctx)
    return {"removed": args.name, "active": ctx.state.current}


def use(ctx, args) -> dict:
    ctx.need_data_dir()
    if args.name not in ctx.state.profiles:
        raise UsageError(f"no profile {args.name!r}; `ppx env ls` lists them")
    previous = ctx.state.current
    ctx.state.current = args.name
    ctx.changes.add(f"make {args.name} the active profile", "this computer",
                    undo=f"ppx env use {previous}" if previous else None)
    _save(ctx)
    return {"active": args.name}


def ls(ctx, args) -> dict:
    items = [{**p.to_dict(), "active": name == ctx.state.current} for name, p in ctx.state.profiles.items()]
    return {"count": len(items), "notice_mode": ctx.state.notice_mode, "items": items}
