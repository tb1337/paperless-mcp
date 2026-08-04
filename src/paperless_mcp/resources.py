"""The master-data resources, declared once.

Five tables used to enumerate the same handful of resources: the relations
``_relations`` resolves names under, the object types
``/api/bulk_edit_objects/`` accepts, the lookups a :class:`~paperless_mcp.names.NameMap`
carries, the keys a suggestions payload names its ID lists after, and the
categories ``search_everywhere`` reports. Adding a resource meant editing five
places, and four of them failing silently if you missed one.

This module sits above both layers — ``tools/`` and ``formatting``/``names`` — so
each of those derives from it instead of restating it. It deliberately holds no
imports from either: a resource is a name, a service to reach it through and a
projection, and the projection is passed in by whoever owns it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from .names import NameLookup, NameMap

if TYPE_CHECKING:
    from pypaperless import PaperlessClient


@dataclass(frozen=True, slots=True)
class Resource:
    """One master-data resource, as every table over them saw it.

    Args:
        key: Plural name. The list tool's suffix, its envelope key, the
            ``search_everywhere`` category, the ``/api/bulk_edit_objects/``
            ``object_type``, and the suggestions payload's ID-list key.
        singular: Singular name. The ``<field>_id`` / ``<field>_name`` argument
            prefix, the relation ``_relations`` resolves under, and the key a
            create or delete reports its object as.
        service: Picks this resource's service off a connected client.
        names_field: The :class:`~paperless_mcp.names.NameMap` field holding its
            ``id -> name`` lookup, or ``None`` for a resource no relation
            argument points at.
        bulk_editable: Whether ``/api/bulk_edit_objects/`` has a branch for it.
            Custom fields have none, which is why ``delete_custom_field`` stays
            the only way to remove one.
    """

    key: str
    singular: str
    service: Callable[[PaperlessClient], Any]
    names_field: str | None = None
    bulk_editable: bool = False

    def lookup(self, names: NameMap) -> NameLookup:
        """This resource's ``id -> name`` lookup, empty when it has none.

        Read off the snapshot by field name, so the relation arguments, the
        lookups they resolve against and the list tools they point at stay one
        list rather than three.
        """
        return getattr(names, self.names_field) if self.names_field else {}

    @property
    def list_tool(self) -> str:
        """The tool that lists this resource in full, named by convention."""
        return f"list_{self.key}"

    @property
    def id_argument(self) -> str:
        """The argument a single-object tool takes, named by convention."""
        return f"{self.singular}_id"


TAGS: Final = Resource(
    "tags", "tag", lambda paperless: paperless.tags, names_field="tags", bulk_editable=True
)
CORRESPONDENTS: Final = Resource(
    "correspondents",
    "correspondent",
    lambda paperless: paperless.correspondents,
    names_field="correspondents",
    bulk_editable=True,
)
DOCUMENT_TYPES: Final = Resource(
    "document_types",
    "document_type",
    lambda paperless: paperless.document_types,
    names_field="document_types",
    bulk_editable=True,
)
STORAGE_PATHS: Final = Resource(
    "storage_paths",
    "storage_path",
    lambda paperless: paperless.storage_paths,
    names_field="storage_paths",
    bulk_editable=True,
)
CUSTOM_FIELDS: Final = Resource(
    "custom_fields", "custom_field", lambda paperless: paperless.custom_fields
)

#: Every master-data resource the tool surface carries, in the order the CRUD
#: tools are registered.
RESOURCES: Final[tuple[Resource, ...]] = (
    TAGS,
    CORRESPONDENTS,
    DOCUMENT_TYPES,
    STORAGE_PATHS,
    CUSTOM_FIELDS,
)

#: By the relation name the tool arguments spell — the resources a ``<field>_name``
#: can be resolved against.
RELATIONS: Final[Mapping[str, Resource]] = {
    resource.singular: resource for resource in RESOURCES if resource.names_field
}

#: By ``object_type``, for the endpoint that deletes many at once.
BULK_OBJECTS: Final[Mapping[str, Resource]] = {
    resource.key: resource for resource in RESOURCES if resource.bulk_editable
}
