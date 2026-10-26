"""sp verbs. Data only; see verbdefs for the fields and effects."""

from __future__ import annotations

from ppx.verbdefs import Verb, a

SITE = a("--site", help="site URL, or a name remembered by `sp site search` (default: the profile's site)")
LIST = a("--list", required=True, help="list title, id, or server-relative URL")
OUT = a("--out", help="file or folder to write to (default: the ppx exports folder)")
SET = a("--set", required=True, help="column values: Name=Value, a JSON object, or @file.json (internal names)")
TEMPLATES = ("GenericList", "DocumentLibrary", "Tasks", "Events", "Contacts", "Announcements", "Links",
             "IssuesTracking", "PictureLibrary")
FIELD_TYPES = ("Text", "Note", "Number", "Currency", "DateTime", "Boolean", "Choice", "MultiChoice", "User")

VERBS: tuple[Verb, ...] = (
    Verb(("sp", "site", "get"), "Show a site (title, URL, template, last change) and remember its name",
         "read", "ppx.sp.sites:get",
         args=(a("site", required=False, help="site URL or remembered name (default: the profile's site)"),)),
    Verb(("sp", "site", "search"), "Find sites by keyword and remember their URLs for --site", "read",
         "ppx.sp.sites:search", args=(a("query", help="words in the site's name or description"),)),
    Verb(("sp", "list", "ls"), "Lists and libraries of a site with item counts", "read", "ppx.sp.lists:ls",
         args=(SITE,)),
    Verb(("sp", "list", "get"), "One list: item count, kind, versioning, URL",
         "read", "ppx.sp.lists:get", args=(SITE, LIST)),
    Verb(("sp", "fields", "ls"), "Columns of a list: types, indexes, delegation and connector notes", "read",
         "ppx.sp.fields:ls", args=(SITE, LIST)),
    Verb(("sp", "items", "ls"), "One page of list items; --fields picks columns",
         "read", "ppx.sp.items:ls",
         args=(SITE, LIST, a("--filter", help="OData filter on internal names, e.g. \"Status eq 'Open'\""),
               a("--page-size", kind="int", default=500, help="items per page, 1 to 5000 (--top overrides)"),
               a("--page", kind="int", default=1, help="page number, from 1"))),
    Verb(("sp", "items", "get"), "One list item by id", "read", "ppx.sp.items:get",
         args=(SITE, LIST, a("--id", kind="int", required=True, help="item id"))),
    Verb(("sp", "files", "ls"), "Files and subfolders of a folder", "read", "ppx.sp.files:ls",
         args=(SITE, a("--folder", required=True,
                       help="server- or site-relative folder path, e.g. \"Shared Documents/Reports\""),
               a("--recursive", kind="flag", help="include every subfolder"))),
    Verb(("sp", "files", "get"), "Download one file; returns its local path", "read",
         "ppx.sp.files:get",
         notice="Save SharePoint file {file} to {out} on this computer (--yes overwrites a file already there)",
         args=(SITE, a("file", help="server- or site-relative file path, or a file name with --folder"),
               a("--folder", help="folder holding the file"), OUT),
         escalate=(("--out", "local"), ("--yes", "irreversible"))),
    Verb(("sp", "export"), "Write every row of a list to a CSV or JSON file",
         "read", "ppx.sp.export:run",
         notice="Export SharePoint list {list} to {out} on this computer (--yes overwrites a file already there)",
         args=(SITE, LIST, a("--format", kind="choice", choices=("csv", "json"), default="csv", help="file format"),
               OUT),
         escalate=(("--out", "local"), ("--yes", "irreversible"))),
    # -- writes (phase 5) ---------------------------------------------------------------------------
    Verb(("sp", "list", "add"), "Create a list or library on a site", "change", "ppx.sp.lists_write:list_add",
         notice="Create SharePoint list {title} on site {site}",
         args=(SITE, a("--title", required=True, help="list title"),
               a("--template", kind="choice", choices=TEMPLATES, default="GenericList",
                 help="list template (default GenericList; DocumentLibrary for a library)"))),
    Verb(("sp", "fields", "add"), "Add a column to a list (added to its default view)", "change",
         "ppx.sp.lists_write:field_add", notice="Add {type} column {name} to SharePoint list {list} on site {site}",
         args=(SITE, LIST, a("--name", required=True, help="column display name"),
               a("--type", kind="choice", choices=FIELD_TYPES, required=True, help="column type"),
               a("--choices", help="Choice and MultiChoice values separated by ;"),
               a("--required", kind="flag", help="make the column required"))),
    Verb(("sp", "items", "add"), "Add one list item", "change", "ppx.sp.items_write:add",
         notice="Add an item ({set}) to SharePoint list {list} on site {site}", args=(SITE, LIST, SET)),
    Verb(("sp", "items", "set"), "Update one list item, refusing if it changed since it was read (eTag)", "change",
         "ppx.sp.items_write:set_", notice="Update item {id} ({set}) of SharePoint list {list} on site {site}",
         args=(SITE, LIST, a("--id", kind="int", required=True, help="item id"), SET,
               a("--etag", help="the eTag you read the item at (default: read it now)"))),
    # More than 50 items, or --permanent, needs --yes; ppx enforces that itself.
    Verb(("sp", "items", "rm"), "Delete list items to the recycle bin (--filter needs a --dry-run first)", "change",
         "ppx.sp.items_write:rm",
         notice="Delete items (ids: {id}; filter: {filter}) of SharePoint list {list} on site {site}: to the recycle "
                "bin, or for good with --permanent",
         args=(SITE, LIST, a("--id", help="item id, or ids separated by commas"),
               a("--filter", help="OData filter selecting the items, e.g. \"Status eq 'Closed'\""),
               a("--permanent", kind="flag", help="skip the recycle bin (needs --yes)")),
         escalate=(("--permanent", "irreversible"), ("--yes", "irreversible"))),
    Verb(("sp", "files", "put"), "Upload a file (up to 90 MB) to a folder; replacing one needs --overwrite --yes",
         "change", "ppx.sp.files_write:put",
         notice="Upload {file} to SharePoint folder {folder} on site {site} (--overwrite replaces a file of that name)",
         args=(a("file", help="the local file"), SITE,
               a("--folder", required=True, help="server- or site-relative folder, e.g. \"Shared Documents/Reports\""),
               a("--name", help="file name in SharePoint (default: the local name)"),
               a("--overwrite", kind="flag", help="replace a file of the same name (backed up first; needs --yes)")),
         escalate=(("--overwrite", "irreversible"),)),
    Verb(("sp", "m-snippet"), "A list as a TMDL table: SharePoint.Tables 2.0 partition with typed columns", "read",
         "ppx.sp.msnippet:run",
         notice="Write the TMDL table for SharePoint list {list} to {out} on this computer",
         args=(SITE, LIST, a("--view", kind="choice", choices=("Default", "All"), default="Default",
                             help="ViewMode: the list's default view (Default) or every column (All)"), OUT),
         escalate=(("--out", "local"), ("--yes", "irreversible"))),
)
