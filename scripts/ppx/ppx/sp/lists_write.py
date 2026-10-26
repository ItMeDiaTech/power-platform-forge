"""sp list add and sp fields add: create a list, and add a column to one.

`m365 spo list add --title --webUrl --baseTemplate` (https://pnp.github.io/cli-microsoft365/cmd/spo/list/list-add/)
and `m365 spo field add --webUrl --list... --xml <CAML> --options AddFieldToDefaultView`
(.../spo/field/field-add/: the column is defined only by its CAML XML; SharePoint derives the internal
name from DisplayName unless AddFieldInternalNameHint is given).
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from ppx import m365
from ppx.errors import UsageError
from ppx.sp import lists, sites


def list_add(ctx, args) -> dict:
    site = sites.resolve(ctx, args.site)
    ctx.changes.add(f"create a {args.template} named {args.title}", f"site {site}",
                    undo=f"delete {args.title} under Site contents (it goes to the recycle bin)")
    out = {"site": site, "title": args.title, "template": args.template}
    if ctx.dry_run:
        return out
    made = m365.run(["spo", "list", "add", "--title", args.title, "--webUrl", site, "--baseTemplate", args.template],
                    timeout=ctx.timeout) or {}
    ctx.next("add columns", command=f'ppx sp fields add --site "{site}" --list "{args.title}" --name <column> '
                                    "--type Text")
    return {**out, "id": made.get("Id"), "url": (made.get("RootFolder") or {}).get("ServerRelativeUrl")}


# m365 is a .cmd shim on Windows and proc.cmd_quote refuses any argument holding a double quote, so the
# CAML uses single-quoted attributes and never carries a literal " (XML allows both quote styles).
_QUOTES = {"'": "&apos;", '"': "&quot;"}


def _attr(value: str) -> str:
    return "'" + escape(value, _QUOTES) + "'"


def field_xml(name: str, kind: str, *, required: bool = False, choices: list[str] | None = None) -> str:
    """CAML for one column; DisplayName and Name are the given name."""
    extra = " UserSelectionMode='PeopleOnly'" if kind == "User" else ""
    inner = ""
    if kind in ("Choice", "MultiChoice"):
        if not choices:
            raise UsageError(f"a {kind} column needs --choices \"First;Second\"")
        inner = "<CHOICES>" + "".join(f"<CHOICE>{escape(c, _QUOTES)}</CHOICE>" for c in choices) + "</CHOICES>"
    elif choices:
        raise UsageError("--choices applies to Choice and MultiChoice columns only")
    return (f"<Field Type={_attr(kind)} DisplayName={_attr(name)} Name={_attr(name)} "
            f"Required='{'TRUE' if required else 'FALSE'}'{extra}>{inner}</Field>")


def field_add(ctx, args) -> dict:
    name = args.name.strip()
    if not name or len(name) > 255:
        raise UsageError("--name must be 1 to 255 characters")
    if any(ch in name + (args.choices or "") for ch in "%\r\n\x00"):
        raise UsageError("--name and --choices cannot hold % or a line break: m365 runs through a Windows batch "
                         "file that would expand or split them; add such a column in the list settings instead")
    choices = [c.strip() for c in (args.choices or "").split(";") if c.strip()]
    xml = field_xml(name, args.type, required=args.required, choices=choices)
    site = sites.resolve(ctx, args.site)
    ctx.changes.add(f"add a {args.type} column {name}" + (" (required)" if args.required else ""),
                    f"list {args.list} on {site}",
                    undo=f"delete column {name} in the list settings (its values go with it)")
    out = {"site": site, "list": args.list, "name": name, "type": args.type}
    if ctx.dry_run:
        return {**out, "xml": xml}
    made = m365.run(["spo", "field", "add", "--webUrl", site, *lists.ref(args.list, prefix="list"), "--xml", xml,
                     "--options", "AddFieldToDefaultView"], timeout=ctx.timeout) or {}
    return {**out, "internal_name": made.get("InternalName"), "id": made.get("Id")}
