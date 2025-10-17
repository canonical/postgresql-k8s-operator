# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
r"""Library to manage the relation for the data-platform products.

This V1 has been specified in https://docs.google.com/document/d/1lnuonWnoQb36RWYwfHOBwU0VClLbawpTISXIC_yNKYo, and should be backward compatible with v0 clients.

This library contains the Requires and Provides classes for handling the relation
between an application and multiple managed application supported by the data-team:
MySQL, Postgresql, MongoDB, Redis, Kafka, and Karapace.

#### Models

This library exposes basic default models that can be used in most cases.
If you need more complex models, you can subclass them.

```python
from charms.data_platform_libs.v1.data_interfaces import RequirerCommonModel, ExtraSecretStr

class ExtendedCommonModel(RequirerCommonModel):
    operator_password: ExtraSecretStr
```

Secret groups are handled using annotated types. If you wish to add extra secret groups, please follow the following model. The string metadata represents the secret group name, and `OptionalSecretStr` is a TypeAlias for `SecretStr | None`. Finally, `SecretStr` represents a field validating the URI pattern `secret:.*`

```python
MyGroupSecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "mygroup"]
```

Fields not specified as OptionalSecretStr and extended with a group name in the metadata will NOT get serialised.


#### Requirer Charm

This library is a uniform interface to a selection of common database
metadata, with added custom events that add convenience to database management,
and methods to consume the application related data.


```python
from charms.data_platform_libs.v1.data_interfaces import (
    RequirerCommonModel,
    RequirerDataContractV1,
    ResourceCreatedEvent,
    ResourceEntityCreatedEvent,
    ResourceProviderModel,
    ResourceRequirerEventHandler,
)

class ClientCharm(CharmBase):
    # Database charm that accepts connections from application charms.
    def __init__(self, *args) -> None:
        super().__init__(*args)

        requests = [
            RequirerCommonModel(
                resource="clientdb",
            ),
            RequirerCommonModel(
                resource="clientbis",
            ),
            RequirerCommonModel(
                entity_type="USER",
            )
        ]
        self.database = ResourceRequirerEventHandler(
            self,"database", requests, response_model=ResourceProviderModel
        )
        self.framework.observe(self.database.on.resource_created, self._on_resource_created)
        self.framework.observe(self.database.on.resource_entity_created, self._on_resource_entity_created)

    def _on_resource_created(self, event: ResourceCreatedEvent) -> None:
        # Event triggered when a new database is created.
        relation_id = event.relation.id
        response = event.response # This is the response model

        username = event.response.username
        password = event.response.password
        ...

    def _on_resource_entity_created(self, event: ResourceCreatedEvent) -> None:
        # Event triggered when a new entity is created.
        ...

Compared to V0, this library makes heavy use of pydantic models, and allows for
multiple requests, specified as a list.
On the Requirer side, each response will trigger one custom event for that response.
This way, it allows for more strategic events to be emitted according to the request.

As show above, the library provides some custom events to handle specific situations, which are listed below:
-  resource_created: event emitted when the requested database is created.
-  resource_entity_created: event emitted when the requested entity is created.
-  endpoints_changed: event emitted when the read/write endpoints of the database have changed.
-  read_only_endpoints_changed: event emitted when the read-only endpoints of the database
  have changed. Event is not triggered if read/write endpoints changed too.

If it is needed to connect multiple database clusters to the same relation endpoint
the application charm can implement the same code as if it would connect to only
one database cluster (like the above code example).

To differentiate multiple clusters connected to the same relation endpoint
the application charm can use the name of the remote application:

```python

def _on_resource_created(self, event: ResourceCreatedEvent) -> None:
    # Get the remote app name of the cluster that triggered this event
    cluster = event.relation.app.name
```

It is also possible to provide an alias for each different database cluster/relation.

So, it is possible to differentiate the clusters in two ways.
The first is to use the remote application name, i.e., `event.relation.app.name`, as above.

The second way is to use different event handlers to handle each cluster events.
The implementation would be something like the following code:

```python

from charms.data_platform_libs.v1.data_interfaces import (
    RequirerCommonModel,
    RequirerDataContractV1,
    ResourceCreatedEvent,
    ResourceEntityCreatedEvent,
    ResourceProviderModel,
    ResourceRequirerEventHandler,
)

class ApplicationCharm(CharmBase):
    # Application charm that connects to database charms.

    def __init__(self, *args):
        super().__init__(*args)

        requests = [
            RequirerCommonModel(
                resource="clientdb",
            ),
            RequirerCommonModel(
                resource="clientbis",
            ),
        ]
        # Define the cluster aliases and one handler for each cluster database created event.
        self.database = ResourceRequirerEventHandler(
            self,
            relation_name="database"
            relations_aliases = ["cluster1", "cluster2"],
            requests=
        )
        self.framework.observe(
            self.database.on.cluster1_resource_created, self._on_cluster1_resource_created
        )
        self.framework.observe(
            self.database.on.cluster2_resource_created, self._on_cluster2_resource_created
        )

    def _on_cluster1_resource_created(self, event: ResourceCreatedEvent) -> None:
        # Handle the created database on the cluster named cluster1

        # Create configuration file for app
        config_file = self._render_app_config_file(
            event.response.username,
            event.response.password,
            event.response.endpoints,
        )
        ...

    def _on_cluster2_resource_created(self, event: ResourceCreatedEvent) -> None:
        # Handle the created database on the cluster named cluster2

        # Create configuration file for app
        config_file = self._render_app_config_file(
            event.response.username,
            event.response.password,
            event.response.endpoints,
        )
        ...
```

### Provider Charm

Following an example of using the ResourceRequestedEvent, in the context of the
database charm code:

```python
from charms.data_platform_libs.v1.data_interfaces import (
    ResourceProviderEventHandler,
    ResourceProviderModel,
    ResourceRequestedEvent,
    RequirerCommonModel,
)

class SampleCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)
        # Charm events defined in the database provides charm library.
        self.provided_database = ResourceProviderEventHandler(self, "database", RequirerCommonModel)
        self.framework.observe(self.provided_database.on.resource_requested,
            self._on_resource_requested)
        # Database generic helper
        self.database = DatabaseHelper()

    def _on_database_requested(self, event: ResourceRequestedEvent) -> None:
        # Handle the event triggered by a new database requested in the relation
        # Retrieve the database name using the charm library.
        db_name = event.request.resource
        # generate a new user credential
        username = self.database.generate_user(event.request.request_id)
        password = self.database.generate_password(event.request.request_id)
        # set the credentials for the relation
        response = ResourceProviderModel(
            salt=event.request.salt,
            request_id=event.request.request_id,
            resource=db_name,
            username=SecretStr(username),
            password=SecretStr(password),
            ...
        )
        self.provided_database.set_response(event.relation.id, response)
```

As shown above, the library provides a custom event (database_requested) to handle
the situation when an application charm requests a new database to be created.
It's preferred to subscribe to this event instead of relation changed event to avoid
creating a new database when other information other than a database name is
exchanged in the relation databag.

"""

import copy
import hashlib
import json
import logging
import pickle
import random
import string
from abc import ABC, abstractmethod
from collections import namedtuple
from datetime import datetime
from enum import Enum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Generic,
    Literal,
    NewType,
    TypeAlias,
    TypeVar,
    final,
    overload,
)

from ops import (
    CharmBase,
    EventBase,
    Model,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationEvent,
    Secret,
    SecretChangedEvent,
    SecretInfo,
    SecretNotFoundError,
)
from ops.charm import CharmEvents, SecretRemoveEvent
from ops.framework import EventSource, Handle, Object
from ops.model import Application, ModelError, Relation, Unit
from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SecretStr,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    Tag,
    TypeAdapter,
    ValidationInfo,
    model_serializer,
    model_validator,
)
from pydantic.types import _SecretBase, _SecretField
from pydantic_core import CoreSchema, core_schema
from typing_extensions import TypeAliasType, override

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# The unique Charmhub library identifier, never change it
LIBID = "6c3e6b6680d64e9c89e611d1a15f65be"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 0

PYDEPS = ["ops>=2.0.0", "pydantic>=2.11"]

logger = logging.getLogger(__name__)

MODEL_ERRORS = {
    "not_leader": "this unit is not the leader",
    "no_label_and_uri": "ERROR either URI or label should be used for getting an owned secret but not both",
    "owner_no_refresh": "ERROR secret owner cannot use --refresh",
}

RESOURCE_ALIASES = [
    "database",
    "subject",
    "topic",
    "index",
    "plugin-url",
    "prefix",
]

SECRET_PREFIX = "secret-"


##############################################################################
# Exceptions
##############################################################################


class DataInterfacesError(Exception):
    """Common ancestor for DataInterfaces related exceptions."""


class SecretError(DataInterfacesError):
    """Common ancestor for Secrets related exceptions."""


class SecretAlreadyExistsError(SecretError):
    """A secret that was to be added already exists."""


class SecretsUnavailableError(SecretError):
    """Secrets aren't yet available for Juju version used."""


class IllegalOperationError(DataInterfacesError):
    """To be used when an operation is not allowed to be performed."""


##############################################################################
# Global helpers / utilities
##############################################################################


def gen_salt() -> str:
    """Generates a consistent salt."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=16))


def gen_hash(resource_name: str, salt: str) -> str:
    """Generates a consistent hash based on the resource name and salt."""
    hasher = hashlib.sha256()
    hasher.update(f"{resource_name}:{salt}".encode())
    return hasher.hexdigest()[:16]


def ensure_leader_for_app(f):
    """Decorator to ensure that only leader can perform given operation."""

    def wrapper(self, *args, **kwargs):
        if self.component == self._local_app and not self._local_unit.is_leader():
            logger.error(f"This operation ({f.__name__}) can only be performed by the leader unit")
            return
        return f(self, *args, **kwargs)

    return wrapper


def get_encoded_dict(
    relation: Relation, member: Unit | Application, field: str
) -> dict[str, Any] | None:
    """Retrieve and decode an encoded field from relation data."""
    data = json.loads(relation.data[member].get(field, "{}"))
    if isinstance(data, dict):
        return data
    logger.error("Unexpected datatype for %s instead of dict.", str(data))


Diff = namedtuple("Diff", ["added", "changed", "deleted"])
Diff.__doc__ = """
A tuple for storing the diff between two data mappings.

added - keys that were added
changed - keys that still exist but have new values
deleted - key that were deleted"""


def diff(old_data: dict[str, str] | None, new_data: dict[str, str]) -> Diff:
    """Retrieves the diff of the data in the relation changed databag for v1.

    Args:
        old_data: dictionary of the stored data before the event.
        new_data: dictionary of the received data to compute the diff.

    Returns:
        a Diff instance containing the added, deleted and changed
            keys from the event relation databag.
    """
    old_data = old_data or {}

    # These are the keys that were added to the databag and triggered this event.
    added = new_data.keys() - old_data.keys()
    # These are the keys that were removed from the databag and triggered this event.
    deleted = old_data.keys() - new_data.keys()
    # These are the keys that already existed in the databag,
    # but had their values changed.
    changed = {key for key in old_data.keys() & new_data.keys() if old_data[key] != new_data[key]}
    # Return the diff with all possible changes.
    return Diff(added, changed, deleted)


def resource_added(diff: Diff) -> bool:
    """Ensures that one of the aliased resources has been added."""
    return any(item in diff.added for item in RESOURCE_ALIASES + ["resource"])


def store_new_data(
    relation: Relation,
    component: Unit | Application,
    new_data: dict[str, str],
    short_uuid: str | None = None,
):
    """Stores the new data in the databag for diff computation."""
    # First, the case for V0
    if not short_uuid:
        relation.data[component].update({"data": json.dumps(new_data)})
    # Then the case for V1, where we have a ShortUUID
    else:
        data = json.loads(relation.data[component].get("data", "{}"))
        if not isinstance(data, dict):
            raise ValueError
        newest_data = copy.deepcopy(data)
        newest_data[short_uuid] = new_data
        relation.data[component].update({"data": json.dumps(newest_data)})


##############################################################################
# Helper classes
##############################################################################

SecretGroup = NewType("SecretGroup", str)


SecretString = TypeAliasType("SecretString", Annotated[str, Field(pattern="secret:.*")])


class SecretBool(_SecretField[bool]):
    """Class for booleans as secrets."""

    _inner_schema: ClassVar[CoreSchema] = core_schema.bool_schema()
    _error_kind: ClassVar[str] = "bool_type"

    def _display(self) -> str:
        return "****"


OptionalSecretStr: TypeAlias = SecretStr | None
OptionalSecretBool: TypeAlias = SecretBool | None

OptionalSecrets = (OptionalSecretStr, OptionalSecretBool)

UserSecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "user"]
TlsSecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "tls"]
TlsSecretBool = Annotated[OptionalSecretBool, Field(exclude=True, default=None), "tls"]
MtlsSecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "mtls"]
ExtraSecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "extra"]
EntitySecretStr = Annotated[OptionalSecretStr, Field(exclude=True, default=None), "entity"]


class Scope(Enum):
    """Peer relations scope."""

    APP = "app"
    UNIT = "unit"


class CachedSecret:
    """Locally cache a secret.

    The data structure is precisely reusing/simulating as in the actual Secret Storage
    """

    KNOWN_MODEL_ERRORS = [MODEL_ERRORS["no_label_and_uri"], MODEL_ERRORS["owner_no_refresh"]]

    def __init__(
        self,
        model: Model,
        component: Application | Unit,
        label: str,
        secret_uri: str | None = None,
    ):
        self._secret_meta = None
        self._secret_content = {}
        self._secret_uri = secret_uri
        self.label = label
        self._model = model
        self.component = component
        self.current_label = None

    @property
    def meta(self) -> Secret | None:
        """Getting cached secret meta-information."""
        if not self._secret_meta:
            if not (self._secret_uri or self.label):
                return

            try:
                self._secret_meta = self._model.get_secret(label=self.label)
            except SecretNotFoundError:
                # Falling back to seeking for potential legacy labels
                logger.info(f"Secret with label {self.label} not found")

            # If still not found, to be checked by URI, to be labelled with the proposed label
            if not self._secret_meta and self._secret_uri:
                self._secret_meta = self._model.get_secret(id=self._secret_uri, label=self.label)
        return self._secret_meta

    ##########################################################################
    # Public functions
    ##########################################################################

    def add_secret(
        self,
        content: dict[str, str],
        relation: Relation | None = None,
        label: str | None = None,
    ) -> Secret:
        """Create a new secret."""
        if self._secret_uri:
            raise SecretAlreadyExistsError(
                "Secret is already defined with uri %s", self._secret_uri
            )

        label = self.label if not label else label

        secret = self.component.add_secret(content, label=label)
        if relation and relation.app != self._model.app:
            # If it's not a peer relation, grant is to be applied
            secret.grant(relation)
        self._secret_uri = secret.id
        self._secret_meta = secret
        return self._secret_meta

    def get_content(self) -> dict[str, str]:
        """Getting cached secret content."""
        if not self._secret_content:
            if self.meta:
                try:
                    self._secret_content = self.meta.get_content(refresh=True)
                except (ValueError, ModelError) as err:
                    # https://bugs.launchpad.net/juju/+bug/2042596
                    # Only triggered when 'refresh' is set
                    if isinstance(err, ModelError) and not any(
                        msg in str(err) for msg in self.KNOWN_MODEL_ERRORS
                    ):
                        raise
                    # Due to: ValueError: Secret owner cannot use refresh=True
                    self._secret_content = self.meta.get_content()
        return self._secret_content

    def set_content(self, content: dict[str, str]) -> None:
        """Setting cached secret content."""
        if not self.meta:
            return

        if content == self.get_content():
            return

        if content:
            self.meta.set_content(content)
            self._secret_content = content
        else:
            self.meta.remove_all_revisions()

    def get_info(self) -> SecretInfo | None:
        """Wrapper function to apply the corresponding call on the Secret object within CachedSecret if any."""
        if self.meta:
            return self.meta.get_info()

    def remove(self) -> None:
        """Remove secret."""
        if not self.meta:
            raise SecretsUnavailableError("Non-existent secret was attempted to be removed.")
        try:
            self.meta.remove_all_revisions()
        except SecretNotFoundError:
            pass
        self._secret_content = {}
        self._secret_meta = None
        self._secret_uri = None


class SecretCache:
    """A data structure storing CachedSecret objects."""

    def __init__(self, model: Model, component: Application | Unit):
        self._model = model
        self.component = component
        self._secrets: dict[str, CachedSecret] = {}

    def get(self, label: str, uri: str | None = None) -> CachedSecret | None:
        """Getting a secret from Juju Secret store or cache."""
        if not self._secrets.get(label):
            secret = CachedSecret(self._model, self.component, label, uri)
            if secret.meta:
                self._secrets[label] = secret
        return self._secrets.get(label)

    def add(self, label: str, content: dict[str, str], relation: Relation) -> CachedSecret:
        """Adding a secret to Juju Secret."""
        if self._secrets.get(label):
            raise SecretAlreadyExistsError(f"Secret {label} already exists")

        secret = CachedSecret(self._model, self.component, label)
        secret.add_secret(content, relation)
        self._secrets[label] = secret
        return self._secrets[label]

    def remove(self, label: str) -> None:
        """Remove a secret from the cache."""
        if secret := self.get(label):
            try:
                secret.remove()
                self._secrets.pop(label)
            except (SecretsUnavailableError, KeyError):
                pass
            else:
                return
        logging.debug("Non-existing Juju Secret was attempted to be removed %s", label)


##############################################################################
# Models classes
##############################################################################


class PeerModel(BaseModel):
    """Common Model for all peer relations."""

    model_config = ConfigDict(
        validate_by_name=True,
        validate_by_alias=True,
        populate_by_name=True,
        serialize_by_alias=True,
        alias_generator=lambda x: x.replace("_", "-"),
        extra="allow",
    )

    @model_validator(mode="after")
    def extract_secrets(self, info: ValidationInfo):
        """Extract all secret_fields into their local field."""
        if not info.context or not isinstance(info.context.get("repository"), AbstractRepository):
            logger.debug("No secret parsing as we're lacking context here.")
            return self
        repository: AbstractRepository = info.context.get("repository")
        for field, field_info in self.__pydantic_fields__.items():
            if field_info.annotation in OptionalSecrets and len(field_info.metadata) == 1:
                secret_group = SecretGroup(field_info.metadata[0])
                if not secret_group:
                    raise SecretsUnavailableError(field)

                aliased_field = field_info.serialization_alias or field
                secret = repository.get_secret(secret_group, secret_uri=None)

                if not secret:
                    logger.info(f"No secret for group {secret_group}")
                    continue

                value = secret.get_content().get(aliased_field)

                if value and field_info.annotation == OptionalSecretBool:
                    value = SecretBool(json.loads(value))
                elif value:
                    value = SecretStr(value)
                setattr(self, field, value)

        return self

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo):
        """Serializes the model writing the secrets in their respective secrets."""
        if not info.context or not isinstance(info.context.get("repository"), AbstractRepository):
            logger.debug("No secret parsing serialization as we're lacking context here.")
            return handler(self)
        repository: AbstractRepository = info.context.get("repository")

        for field, field_info in self.__pydantic_fields__.items():
            if field_info.annotation in OptionalSecrets and len(field_info.metadata) == 1:
                secret_group = SecretGroup(field_info.metadata[0])
                if not secret_group:
                    raise SecretsUnavailableError(field)

                aliased_field = field_info.serialization_alias or field
                secret = repository.get_secret(secret_group, secret_uri=None)

                value = getattr(self, field)

                actual_value = (
                    value.get_secret_value() if issubclass(value.__class__, _SecretBase) else value
                )
                if not isinstance(actual_value, str):
                    actual_value = json.dumps(actual_value)

                if secret is None:
                    if value:
                        secret = repository.add_secret(
                            aliased_field,
                            actual_value,
                            secret_group,
                        )
                        if not secret or not secret.meta:
                            raise SecretError("No secret to send back")
                    continue

                content = secret.get_content()
                full_content = copy.deepcopy(content)

                if value is None:
                    full_content.pop(aliased_field, None)
                else:
                    full_content.update({aliased_field: actual_value})
                secret.set_content(full_content)
        return handler(self)


class CommonModel(BaseModel):
    """Common Model for both requirer and provider.

    request_id stores the request identifier for easier access.
    resource is the requested resource.
    """

    model_config = ConfigDict(
        validate_by_name=True,
        validate_by_alias=True,
        populate_by_name=True,
        serialize_by_alias=True,
        alias_generator=lambda x: x.replace("_", "-"),
        extra="allow",
    )

    resource: str = Field(validation_alias=AliasChoices(*RESOURCE_ALIASES), default="")
    request_id: str | None = Field(default=None)
    salt: str = Field(
        description="This salt is used to create unique hashes even when other fields map 1-1",
        default_factory=gen_salt,
    )

    @model_validator(mode="after")
    def extract_secrets(self, info: ValidationInfo):
        """Extract all secret_fields into their local field."""
        if not info.context or not isinstance(info.context.get("repository"), AbstractRepository):
            logger.debug("No secret parsing as we're lacking context here.")
            return self
        repository: AbstractRepository = info.context.get("repository")
        short_uuid = self.request_id or gen_hash(self.resource, self.salt)
        for field, field_info in self.__pydantic_fields__.items():
            if field_info.annotation in OptionalSecrets and len(field_info.metadata) == 1:
                secret_group = field_info.metadata[0]
                if not secret_group:
                    raise SecretsUnavailableError(field)

                aliased_field = field_info.serialization_alias or field
                secret_field = repository.secret_field(secret_group, aliased_field).replace(
                    "-", "_"
                )
                secret_uri: str | None = getattr(self, secret_field, None)

                if not secret_uri:
                    continue

                secret = repository.get_secret(
                    secret_group, secret_uri=secret_uri, short_uuid=short_uuid
                )

                if not secret:
                    logger.info(f"No secret for group {secret_group} and short uuid {short_uuid}")
                    continue

                value = secret.get_content().get(aliased_field)
                if value and field_info.annotation == OptionalSecretBool:
                    value = SecretBool(json.loads(value))
                elif value:
                    value = SecretStr(value)

                setattr(self, field, value)
        return self

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: SerializerFunctionWrapHandler, info: SerializationInfo):
        """Serializes the model writing the secrets in their respective secrets."""
        if not info.context or not isinstance(info.context.get("repository"), AbstractRepository):
            logger.debug("No secret parsing serialization as we're lacking context here.")
            return handler(self)
        repository: AbstractRepository = info.context.get("repository")
        short_uuid = self.request_id or gen_hash(self.resource, self.salt)
        # Backward compatibility for v0 regarding secrets.
        if info.context.get("version") == "v0":
            short_uuid = None

        for field, field_info in self.__pydantic_fields__.items():
            if field_info.annotation in OptionalSecrets and len(field_info.metadata) == 1:
                secret_group = field_info.metadata[0]
                if not secret_group:
                    raise SecretsUnavailableError(field)
                aliased_field = field_info.serialization_alias or field
                secret_field = repository.secret_field(secret_group, aliased_field).replace(
                    "-", "_"
                )
                secret_uri: str | None = getattr(self, secret_field, None)
                secret = repository.get_secret(
                    secret_group, secret_uri=secret_uri, short_uuid=short_uuid
                )

                value = getattr(self, field)

                actual_value = (
                    value.get_secret_value() if issubclass(value.__class__, _SecretBase) else value
                )
                if not isinstance(actual_value, str):
                    actual_value = json.dumps(actual_value)

                if secret is None:
                    if value:
                        secret = repository.add_secret(
                            aliased_field, actual_value, secret_group, short_uuid
                        )
                        if not secret or not secret.meta:
                            raise SecretError("No secret to send back")
                        setattr(self, secret_field, secret.meta.id)
                    continue

                content = secret.get_content()
                full_content = copy.deepcopy(content)

                if value is None:
                    full_content.pop(aliased_field, None)
                else:
                    full_content.update({aliased_field: actual_value})
                secret.set_content(full_content)

                if not full_content:
                    # Setting a field to '' deletes it
                    setattr(self, secret_field, None)
                    repository.delete_secret(secret.label)

        return handler(self)

    @classmethod
    def _get_secret_field(cls, field: str) -> SecretGroup | None:
        """Checks if the field is a secret uri or not."""
        if not field.startswith(SECRET_PREFIX):
            return None

        value = field.split("-")[1]
        if info := cls.__pydantic_fields__.get(field.replace("-", "_")):
            if info.annotation == SecretString:
                return SecretGroup(value)
        return None


class EntityPermissionModel(BaseModel):
    """Entity Permissions Model."""

    resource_name: str
    resource_type: str
    privileges: list


class RequirerCommonModel(CommonModel):
    """Requirer side of the request model.

    extra_user_roles is used to request more roles for that user.
    external_node_connectivity is used to indicate that the URI should be made for external clients when True
    """

    extra_user_roles: str | None = Field(default=None)
    extra_group_roles: str | None = Field(default=None)
    external_node_connectivity: bool = Field(default=False)
    entity_type: Literal["USER", "GROUP"] | None = Field(default=None)
    entity_permissions: list[EntityPermissionModel] | None = Field(default=None)
    secret_mtls: SecretString | None = Field(default=None)
    mtls_cert: MtlsSecretStr = Field(default=None)

    @model_validator(mode="after")
    def validate_fields(self):
        """Validates that no inconsistent request is being sent."""
        if self.entity_type and self.entity_type not in ["USER", "GROUP"]:
            raise ValueError("Invalid entity-type. Possible values are USER and GROUP")

        if self.entity_type == "USER" and self.extra_group_roles:
            raise ValueError("Inconsistent entity information. Use extra_user_roles instead")

        if self.entity_type == "GROUP" and self.extra_user_roles:
            raise ValueError("Inconsistent entity information. Use extra_group_roles instead")

        return self


class ProviderCommonModel(CommonModel):
    """Serialized fields added to the databag.

    endpoints stores the endpoints exposed to that client.
    secret_user is a secret URI mapping to the user credentials
    secret_tls is a secret URI mapping to the TLS certificate
    secret_extra is a secret URI for all additional secrets requested.
    """

    endpoints: str | None = Field(default=None)
    read_only_endpoints: str | None = Field(default=None)
    secret_user: SecretString | None = Field(default=None)
    secret_tls: SecretString | None = Field(default=None)
    secret_extra: SecretString | None = Field(default=None)
    secret_entity: SecretString | None = Field(default=None)


class ResourceProviderModel(ProviderCommonModel):
    """Extended model including the deserialized fields."""

    username: UserSecretStr = Field(default=None)
    password: UserSecretStr = Field(default=None)
    uris: UserSecretStr = Field(default=None)
    read_only_uris: UserSecretStr = Field(default=None)
    tls: TlsSecretBool = Field(default=None)
    tls_ca: TlsSecretStr = Field(default=None)
    entity_name: EntitySecretStr = Field(default=None)
    entity_password: EntitySecretStr = Field(default=None)
    version: str | None = Field(default=None)


class RequirerDataContractV0(RequirerCommonModel):
    """Backward compatibility."""

    version: Literal["v0"] = Field(default="v0")

    original_field: str = Field(exclude=True, default="")

    @model_validator(mode="before")
    @classmethod
    def ensure_original_field(cls, data: Any):
        """Ensures that we keep the original field."""
        if isinstance(data, dict):
            for alias in RESOURCE_ALIASES:
                if data.get(alias) is not None:
                    data["original_field"] = alias
                    break
        else:
            for alias in RESOURCE_ALIASES:
                if getattr(data, alias) is not None:
                    data.original_field = alias
        return data


TResourceProviderModel = TypeVar("TResourceProviderModel", bound=ResourceProviderModel)
TRequirerCommonModel = TypeVar("TRequirerCommonModel", bound=RequirerCommonModel)


class RequirerDataContractV1(BaseModel, Generic[TRequirerCommonModel]):
    """The new Data Contract."""

    version: Literal["v1"] = Field(default="v1")
    requests: list[TRequirerCommonModel] = Field(default_factory=list)


def discriminate_on_version(payload: Any) -> str:
    """Use the version to discriminate."""
    if isinstance(payload, dict):
        return payload.get("version", "v0")
    return getattr(payload, "version", "v0")


RequirerDataContractType = Annotated[
    Annotated[RequirerDataContractV0, Tag("v0")] | Annotated[RequirerDataContractV1, Tag("v1")],
    Discriminator(discriminate_on_version),
]


RequirerDataContract = TypeAdapter(RequirerDataContractType)


class DataContractV0(ResourceProviderModel):
    """The Data contract of the response, for V0."""


class DataContractV1(BaseModel, Generic[TResourceProviderModel]):
    """The Data contract of the response, for V1."""

    version: Literal["v1"] = Field(default="v1")
    requests: list[TResourceProviderModel] = Field(default_factory=list)


DataContact = TypeAdapter(DataContractV1[ResourceProviderModel])


TCommonModel = TypeVar("TCommonModel", bound=CommonModel)


def is_topic_value_acceptable(value: str | None) -> str | None:
    """Check whether the given Kafka topic value is acceptable."""
    if value and "*" in value[:3]:
        raise ValueError(f"Error on topic '{value}',, unacceptable value.")
    return value


class KafkaRequestModel(RequirerCommonModel):
    """Specialised model for Kafka."""

    consumer_group_prefix: Annotated[str | None, AfterValidator(is_topic_value_acceptable)] = (
        Field(default=None)
    )


class KafkaResponseModel(ResourceProviderModel):
    """Kafka response model."""

    consumer_group_prefix: ExtraSecretStr = Field(default=None)
    zookeeper_uris: ExtraSecretStr = Field(default=None)


##############################################################################
# AbstractRepository class
##############################################################################


class AbstractRepository(ABC):
    """Abstract repository interface."""

    @abstractmethod
    def get_secret(
        self, secret_group, secret_uri: str | None, short_uuid: str | None = None
    ) -> CachedSecret | None:
        """Gets a secret from the secret cache by uri or label."""
        ...

    @abstractmethod
    def get_secret_field(
        self,
        field: str,
        secret_group: SecretGroup,
        short_uuid: str | None = None,
    ) -> str | None:
        """Gets a value for a field stored in a secret group."""
        ...

    @abstractmethod
    def get_field(self, field: str) -> str | None:
        """Gets the value for one field."""
        ...

    @abstractmethod
    def get_fields(self, *fields: str) -> dict[str, str | None]:
        """Gets the values for all provided fields."""
        ...

    @abstractmethod
    def write_field(self, field: str, value: Any) -> None:
        """Writes the value in the field, without any secret support."""
        ...

    @abstractmethod
    def write_fields(self, mapping: dict[str, Any]) -> None:
        """Writes the values of mapping in the fields without any secret support (keys of mapping)."""
        ...

    def write_secret_field(
        self, field: str, value: Any, group: SecretGroup
    ) -> CachedSecret | None:
        """Writes a secret field."""
        ...

    @abstractmethod
    def add_secret(
        self,
        field: str,
        value: Any,
        secret_group: SecretGroup,
        short_uuid: str | None = None,
    ) -> CachedSecret | None:
        """Gets a value for a field stored in a secret group."""
        ...

    @abstractmethod
    def delete_secret(self, label: str):
        """Deletes a secret by its label."""
        ...

    @abstractmethod
    def delete_field(self, field: str) -> None:
        """Deletes a field."""
        ...

    @abstractmethod
    def delete_fields(self, *fields: str) -> None:
        """Deletes all the provided fields."""
        ...

    @abstractmethod
    def delete_secret_field(self, field: str, secret_group: SecretGroup) -> None:
        """Delete a field stored in a secret group."""
        ...

    @abstractmethod
    def register_secret(self, secret_group: SecretGroup, short_uuid: str | None = None) -> None:
        """Registers a secret using the repository."""
        ...

    @abstractmethod
    def get_data(self) -> dict[str, Any] | None:
        """Gets the whole data."""
        ...

    @abstractmethod
    def secret_field(self, secret_group: SecretGroup, field: str | None = None) -> str:
        """Builds a secret field."""


class OpsRepository(AbstractRepository):
    """Implementation for ops repositories, with some methods left out."""

    SECRET_FIELD_NAME: str

    IGNORES_GROUPS: list[SecretGroup] = []

    uri_to_databag: bool = True

    def __init__(
        self,
        model: Model,
        relation: Relation | None,
        component: Unit | Application,
    ):
        self._local_app = model.app
        self._local_unit = model.unit
        self.relation = relation
        self.component = component
        self.model = model
        self.secrets = SecretCache(model, component)

    @abstractmethod
    def _generate_secret_label(
        self, relation: Relation, secret_group: SecretGroup, short_uuid: str | None = None
    ) -> str:
        """Generate unique group mapping for secrets within a relation context."""
        ...

    @override
    def get_data(self) -> dict[str, Any] | None:
        ret: dict[str, Any] = {}
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None

        for key, value in self.relation.data[self.component].items():
            try:
                ret[key] = json.loads(value)
            except json.JSONDecodeError:
                ret[key] = value

        return ret

    @override
    @ensure_leader_for_app
    def get_field(
        self,
        field: str,
    ) -> str | None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None
        relation_data = self.relation.data[self.component]
        return relation_data.get(field)

    @override
    @ensure_leader_for_app
    def get_fields(self, *fields: str) -> dict[str, str]:
        res = {}
        for field in fields:
            if (value := self.get_field(field)) is not None:
                res[field] = value
        return res

    @override
    @ensure_leader_for_app
    def write_field(self, field: str, value: Any) -> None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None
        if not value:
            return None
        self.relation.data[self.component].update({field: value})

    @override
    @ensure_leader_for_app
    def write_fields(self, mapping: dict[str, Any]) -> None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None
        (self.write_field(field, value) for field, value in mapping.items())

    @override
    @ensure_leader_for_app
    def write_secret_field(
        self, field: str, value: Any, secret_group: SecretGroup
    ) -> CachedSecret | None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None

        label = self._generate_secret_label(self.relation, secret_group)
        secret_uri = self.get_field(self.secret_field(secret_group, field))

        secret = self.secrets.get(label=label, uri=secret_uri)
        if not secret:
            return self.add_secret(field, value, secret_group)
        else:
            content = secret.get_content()
            full_content = copy.deepcopy(content)
            full_content.update({field: value})
            secret.set_content(full_content)
            return secret

    @override
    @ensure_leader_for_app
    def delete_field(self, field: str) -> None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None
        relation_data = self.relation.data[self.component]
        try:
            relation_data.pop(field)
        except KeyError:
            logger.debug(
                f"Non existent field {field} was attempted to be removed from the databag (relation ID: {self.relation.id})"
            )

    @override
    @ensure_leader_for_app
    def delete_fields(self, *fields: str) -> None:
        (self.delete_field(field) for field in fields)

    @override
    @ensure_leader_for_app
    def delete_secret_field(self, field: str, secret_group: SecretGroup) -> None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None

        relation_data = self.relation.data[self.component]
        secret_field = self.secret_field(secret_group, field)

        label = self._generate_secret_label(self.relation, secret_group)
        secret_uri = relation_data.get(secret_field)

        secret = self.secrets.get(label=label, uri=secret_uri)

        if not secret:
            logging.error(f"Can't delete secret for relation {self.relation.id}")
            return None

        content = secret.get_content()
        new_content = copy.deepcopy(content)
        try:
            new_content.pop(field)
        except KeyError:
            logging.debug(
                f"Non-existing secret '{field}' was attempted to be removed"
                f"from relation {self.relation.id} and group {secret_group}"
            )

        # Write the new secret content if necessary
        if new_content:
            secret.set_content(new_content)
            return

        # Remove the secret from the relation if it's fully gone.
        try:
            relation_data.pop(field)
        except KeyError:
            pass
        self.secrets.remove(label)
        return

    @ensure_leader_for_app
    def register_secret(self, uri: str, secret_group: SecretGroup, short_uuid: str | None = None):
        """Registers the secret group for this relation.

        [MAGIC HERE]
        If we fetch a secret using get_secret(id=<ID>, label=<arbitraty_label>),
        then <arbitraty_label> will be "stuck" on the Secret object, whenever it may
        appear (i.e. as an event attribute, or fetched manually) on future occasions.

        This will allow us to uniquely identify the secret on Provider side (typically on
        'secret-changed' events), and map it to the corresponding relation.
        """
        if not self.relation:
            raise ValueError("Cannot register without relation.")

        label = self._generate_secret_label(self.relation, secret_group, short_uuid=short_uuid)
        CachedSecret(self.model, self.component, label, uri).meta

    @override
    def get_secret(
        self, secret_group, secret_uri: str | None, short_uuid: str | None = None
    ) -> CachedSecret | None:
        """Gets a secret from the secret cache by uri or label."""
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None
        if secret_group in self.IGNORES_GROUPS:
            logger.warning(f"Trying to get invalid secret group {secret_group}")
            return None

        label = self._generate_secret_label(self.relation, secret_group, short_uuid=short_uuid)

        return self.secrets.get(label=label, uri=secret_uri)

    @override
    def get_secret_field(
        self,
        field: str,
        secret_group: SecretGroup,
        uri: str | None = None,
        short_uuid: str | None = None,
    ) -> Any | None:
        """Gets a value for a field stored in a secret group."""
        if not self.relation:
            logger.info("No relation to get value from")
            return None
        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None

        if secret_group in self.IGNORES_GROUPS:
            logger.warning(f"Trying to get invalid secret group {secret_group}")
            return None

        secret_field = self.secret_field(secret_group, field)

        relation_data = self.relation.data[self.component]
        secret_uri = uri or relation_data.get(secret_field)
        label = self._generate_secret_label(self.relation, secret_group, short_uuid=short_uuid)

        if self.uri_to_databag and not secret_uri:
            logger.info(f"No secret for group {secret_group} in relation {self.relation}")
            return None

        secret = self.secrets.get(label=label, uri=secret_uri)

        if not secret:
            logger.info(f"No secret for group {secret_group} in relation {self.relation}")
            return None

        content = secret.get_content().get(field)

        if not content:
            return

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content

    @override
    @ensure_leader_for_app
    def add_secret(
        self,
        field: str,
        value: Any,
        secret_group: SecretGroup,
        short_uuid: str | None = None,
    ) -> CachedSecret | None:
        if not self.relation:
            logger.info("No relation to get value from")
            return None

        if self.component not in self.relation.data:
            logger.info(f"Component {self.component} not in relation {self.relation}")
            return None

        if secret_group in self.IGNORES_GROUPS:
            logger.warning(f"Trying to get invalid secret group {secret_group}")

        label = self._generate_secret_label(self.relation, secret_group, short_uuid)

        secret = self.secrets.add(label, {field: value}, self.relation)

        if not secret.meta or not secret.meta.id:
            logging.error("Secret is missing Secret ID")
            raise SecretError("Secret added but is missing Secret ID")

        return secret

    @override
    @ensure_leader_for_app
    def delete_secret(self, label: str) -> None:
        self.secrets.remove(label)


@final
class OpsRelationRepository(OpsRepository):
    """Implementation of the Abstract Repository for non peer relations."""

    SECRET_FIELD_NAME: str = "secret"

    @override
    def _generate_secret_label(
        self, relation: Relation, secret_group: SecretGroup, short_uuid: str | None
    ) -> str:
        """Generate unique group_mappings for secrets within a relation context."""
        if short_uuid:
            return f"{relation.name}.{relation.id}.{short_uuid}.{secret_group}.secret"
        return f"{relation.name}.{relation.id}.{secret_group}.secret"

    def secret_field(self, secret_group: SecretGroup, field: str | None = None) -> str:
        """Generates the field name to store in the peer relation."""
        return f"{self.SECRET_FIELD_NAME}-{secret_group}"

    @ensure_leader_for_app
    @override
    def get_data(self) -> dict[str, Any] | None:
        return super().get_data()


class OpsPeerRepository(OpsRepository):
    """Implementation of the Ops Repository for peer relations."""

    SECRET_FIELD_NAME = "internal_secret"

    IGNORES_GROUPS = [
        SecretGroup("user"),
        SecretGroup("entity"),
        SecretGroup("mtls"),
        SecretGroup("tls"),
    ]

    uri_to_databag: bool = False

    @property
    def scope(self) -> Scope:
        """Returns a scope."""
        if isinstance(self.component, Application):
            return Scope.APP
        if isinstance(self.component, Unit):
            return Scope.UNIT
        raise ValueError("Invalid component, neither a Unit nor an Application")

    @override
    def _generate_secret_label(
        self, relation: Relation, secret_group: SecretGroup, short_uuid: str | None = None
    ) -> str:
        """Generate unique group_mappings for secrets within a relation context."""
        members = [relation.name, self._local_app.name, self.scope.value]

        if secret_group != SecretGroup("extra"):
            members.append(secret_group)
        return f"{'.'.join(members)}"

    def secret_field(self, secret_group: SecretGroup, field: str | None = None) -> str:
        """Generates the field name to store in the peer relation."""
        if not field:
            raise ValueError("Must have a field.")
        return f"{field}@{secret_group}"


@final
class OpsPeerUnitRepository(OpsPeerRepository):
    """Implementation for a unit."""

    @override
    def __init__(self, model: Model, relation: Relation | None, component: Unit):
        super().__init__(model, relation, component)


@final
class OpsOtherPeerUnitRepository(OpsPeerRepository):
    """Implementation for a remote unit."""

    @override
    def __init__(self, model: Model, relation: Relation | None, component: Unit):
        if component == model.unit:
            raise ValueError(f"Can't instantiate {self.__class__.__name__} with local unit.")
        super().__init__(model, relation, component)

    @override
    def write_field(self, field: str, value: Any) -> None:
        raise NotImplementedError("It's not possible to update data of another unit.")

    @override
    def write_fields(self, mapping: dict[str, Any]) -> None:
        raise NotImplementedError("It's not possible to update data of another unit.")

    @override
    def add_secret(
        self, field: str, value: Any, secret_group: SecretGroup, short_uuid: str | None = None
    ) -> CachedSecret | None:
        raise NotImplementedError("It's not possible to update data of another unit.")

    @override
    def delete_field(self, field: str) -> None:
        raise NotImplementedError("It's not possible to update data of another unit.")

    @override
    def delete_fields(self, *fields: str) -> None:
        raise NotImplementedError("It's not possible to update data of another unit.")

    @override
    def delete_secret_field(self, field: str, secret_group: SecretGroup) -> None:
        raise NotImplementedError("It's not possible to update data of another unit.")


TRepository = TypeVar("TRepository", bound=OpsRepository)
TCommon = TypeVar("TCommon", bound=BaseModel)
TPeerCommon = TypeVar("TPeerCommon", bound=PeerModel)
TCommonBis = TypeVar("TCommonBis", bound=BaseModel)


class RepositoryInterface(Generic[TRepository, TCommon]):
    """Repository builder."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        component: Unit | Application,
        repository_type: type[TRepository],
        model: type[TCommon] | TypeAdapter | None,
    ):
        self.charm = charm
        self._model = charm.model
        self.repository_type = repository_type
        self.relation_name = relation_name
        self.model = model
        self.component = component

    @property
    def relations(self) -> list[Relation]:
        """The list of Relation instances associated with this relation name."""
        return self._model.relations[self.relation_name]

    def repository(
        self, relation_id: int, component: Unit | Application | None = None
    ) -> TRepository:
        """Returns a repository for the relation."""
        relation = self._model.get_relation(self.relation_name, relation_id)
        if not relation:
            raise ValueError("Missing relation.")
        return self.repository_type(self._model, relation, component or self.component)

    @overload
    def build_model(
        self,
        relation_id: int,
        model: type[TCommonBis],
        component: Unit | Application | None = None,
    ) -> TCommonBis: ...

    @overload
    def build_model(
        self,
        relation_id: int,
        model: type[TCommon],
        component: Unit | Application | None = None,
    ) -> TCommon: ...

    @overload
    def build_model(
        self,
        relation_id: int,
        model: TypeAdapter[TCommonBis],
        component: Unit | Application | None = None,
    ) -> TCommonBis: ...

    @overload
    def build_model(
        self,
        relation_id: int,
        model: None = None,
        component: Unit | Application | None = None,
    ) -> TCommon: ...

    def build_model(
        self,
        relation_id: int,
        model: type[TCommon] | TypeAdapter[TCommonBis] | None = None,
        component: Unit | Application | None = None,
    ) -> TCommon | TCommonBis:
        """Builds a model using the repository for that relation."""
        model = model or self.model  # First the provided model (allows for specialisation)
        component = component or self.component
        if not model:
            raise ValueError("Missing model to specialise data")
        relation = self._model.get_relation(self.relation_name, relation_id)
        if not relation:
            raise ValueError("Missing relation.")
        return build_model(self.repository_type(self._model, relation, component), model)

    def write_model(
        self, relation_id: int, model: BaseModel, context: dict[str, str] | None = None
    ):
        """Writes the model using the repository."""
        relation = self._model.get_relation(self.relation_name, relation_id)
        if not relation:
            raise ValueError("Missing relation.")

        write_model(
            self.repository_type(self._model, relation, self.component), model, context=context
        )


class OpsRelationRepositoryInterface(RepositoryInterface[OpsRelationRepository, TCommon]):
    """Specialised Interface to build repositories for app peer relations."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        model: type[TCommon] | TypeAdapter | None = None,
    ):
        super().__init__(charm, relation_name, charm.app, OpsRelationRepository, model)


class OpsPeerRepositoryInterface(RepositoryInterface[OpsPeerRepository, TPeerCommon]):
    """Specialised Interface to build repositories for app peer relations."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        model: type[TPeerCommon] | TypeAdapter | None = None,
    ):
        super().__init__(charm, relation_name, charm.app, OpsPeerRepository, model)


class OpsPeerUnitRepositoryInterface(RepositoryInterface[OpsPeerUnitRepository, TPeerCommon]):
    """Specialised Interface to build repositories for this unit peer relations."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        model: type[TPeerCommon] | TypeAdapter | None = None,
    ):
        super().__init__(charm, relation_name, charm.unit, OpsPeerUnitRepository, model)


class OpsOtherPeerUnitRepositoryInterface(
    RepositoryInterface[OpsOtherPeerUnitRepository, TPeerCommon]
):
    """Specialised Interface to build repositories for another unit peer relations."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        unit: Unit,
        model: type[TPeerCommon] | TypeAdapter | None = None,
    ):
        super().__init__(charm, relation_name, unit, OpsOtherPeerUnitRepository, model)


##############################################################################
# DDD implementation methods
##############################################################################
##############################################################################


def build_model(repository: AbstractRepository, model: type[TCommon] | TypeAdapter) -> TCommon:
    """Builds a common model using the provided repository and provided model structure."""
    data = repository.get_data() or {}

    data.pop("data", None)

    # Beware this means all fields should have a default value here.
    if isinstance(model, TypeAdapter):
        return model.validate_python(data, context={"repository": repository})

    return model.model_validate(data, context={"repository": repository})


def write_model(
    repository: AbstractRepository, model: BaseModel, context: dict[str, str] | None = None
):
    """Writes the data stored in the model using the repository object."""
    context = context or {}
    dumped = model.model_dump(
        mode="json", context={"repository": repository} | context, exclude_none=False
    )
    for field, value in dumped.items():
        if value is None:
            repository.delete_field(field)
            continue
        dumped_value = value if isinstance(value, str) else json.dumps(value)
        repository.write_field(field, dumped_value)


##############################################################################
# Custom Events
##############################################################################


class ResourceProviderEvent(EventBase, Generic[TRequirerCommonModel]):
    """Resource requested event.

    Contains the request that should be handled.

    fields to serialize: relation, app, unit, request
    """

    def __init__(
        self,
        handle: Handle,
        relation: Relation,
        app: Application | None,
        unit: Unit | None,
        request: TRequirerCommonModel,
    ):
        super().__init__(handle)
        self.relation = relation
        self.app = app
        self.unit = unit
        self.request = request

    def snapshot(self) -> dict[str, Any]:
        """Save the event information."""
        snapshot = {"relation_name": self.relation.name, "relation_id": self.relation.id}
        if self.app:
            snapshot["app_name"] = self.app.name
        if self.unit:
            snapshot["unit_name"] = self.unit.name
        # The models are too complex and would be blocked by marshal so we pickle dump the model.
        # The full dictionary is pickled afterwards anyway.
        snapshot["request"] = pickle.dumps(self.request)
        return snapshot

    def restore(self, snapshot: dict[str, Any]):
        """Restore event information."""
        relation = self.framework.model.get_relation(
            snapshot["relation_name"], snapshot["relation_id"]
        )
        if not relation:
            raise ValueError("Missing relation")
        self.relation = relation
        self.app = None
        app_name = snapshot.get("app_name")
        if app_name:
            self.app = self.framework.model.get_app(app_name)
        self.unit = None
        unit_name = snapshot.get("unit_name")
        if unit_name:
            self.app = self.framework.model.get_app(unit_name)
        self.request = pickle.loads(snapshot["request"])


class ResourceRequestedEvent(ResourceProviderEvent[TRequirerCommonModel]):
    """Resource requested event."""

    pass


class ResourceEntityRequestedEvent(ResourceProviderEvent[TRequirerCommonModel]):
    """Resource Entity requested event."""

    pass


class ResourceEntityPermissionsChangedEvent(ResourceProviderEvent[TRequirerCommonModel]):
    """Resource entity permissions changed event."""

    pass


class MtlsCertUpdatedEvent(ResourceProviderEvent[TRequirerCommonModel]):
    """Resource entity permissions changed event."""

    def __init__(
        self,
        handle: Handle,
        relation: Relation,
        app: Application | None,
        unit: Unit | None,
        request: TRequirerCommonModel,
        old_mtls_cert: str | None = None,
    ):
        super().__init__(handle, relation, app, unit, request)

        self.old_mtls_cert = old_mtls_cert

    def snapshot(self):
        """Return a snapshot of the event."""
        return super().snapshot() | {"old_mtls_cert": self.old_mtls_cert}

    def restore(self, snapshot):
        """Restore the event from a snapshot."""
        super().restore(snapshot)
        self.old_mtls_cert = snapshot["old_mtls_cert"]


class BulkResourcesRequestedEvent(EventBase, Generic[TRequirerCommonModel]):
    """Resource requested event.

    Contains the request that should be handled.

    fields to serialize: relation, app, unit, request
    """

    def __init__(
        self,
        handle: Handle,
        relation: Relation,
        app: Application | None,
        unit: Unit | None,
        requests: list[TRequirerCommonModel],
    ):
        super().__init__(handle)
        self.relation = relation
        self.app = app
        self.unit = unit
        self.requests = requests

    def snapshot(self) -> dict[str, Any]:
        """Save the event information."""
        snapshot = {"relation_name": self.relation.name, "relation_id": self.relation.id}
        if self.app:
            snapshot["app_name"] = self.app.name
        if self.unit:
            snapshot["unit_name"] = self.unit.name
        # The models are too complex and would be blocked by marshal so we pickle dump the model.
        # The full dictionary is pickled afterwards anyway.
        snapshot["requests"] = [pickle.dumps(request) for request in self.requests]
        return snapshot

    def restore(self, snapshot: dict[str, Any]):
        """Restore event information."""
        relation = self.framework.model.get_relation(
            snapshot["relation_name"], snapshot["relation_id"]
        )
        if not relation:
            raise ValueError("Missing relation")
        self.relation = relation
        self.app = None
        app_name = snapshot.get("app_name")
        if app_name:
            self.app = self.framework.model.get_app(app_name)
        self.unit = None
        unit_name = snapshot.get("unit_name")
        if unit_name:
            self.app = self.framework.model.get_app(unit_name)
        self.requests = [pickle.loads(request) for request in snapshot["requests"]]


class ResourceProvidesEvents(CharmEvents, Generic[TRequirerCommonModel]):
    """Database events.

    This class defines the events that the database can emit.
    """

    bulk_resources_requested = EventSource(BulkResourcesRequestedEvent)
    resource_requested = EventSource(ResourceRequestedEvent)
    resource_entity_requested = EventSource(ResourceEntityRequestedEvent)
    resource_entity_permissions_changed = EventSource(ResourceEntityPermissionsChangedEvent)
    mtls_cert_updated = EventSource(MtlsCertUpdatedEvent)


class ResourceRequirerEvent(EventBase, Generic[TResourceProviderModel]):
    """Resource created/changed event.

    Contains the request that should be handled.

    fields to serialize: relation, app, unit, response
    """

    def __init__(
        self,
        handle: Handle,
        relation: Relation,
        app: Application | None,
        unit: Unit | None,
        response: TResourceProviderModel,
    ):
        super().__init__(handle)
        self.relation = relation
        self.app = app
        self.unit = unit
        self.response = response

    def snapshot(self) -> dict:
        """Save the event information."""
        snapshot = {"relation_name": self.relation.name, "relation_id": self.relation.id}
        if self.app:
            snapshot["app_name"] = self.app.name
        if self.unit:
            snapshot["unit_name"] = self.unit.name
        # The models are too complex and would be blocked by marshal so we pickle dump the model.
        # The full dictionary is pickled afterwards anyway.
        snapshot["response"] = pickle.dumps(self.response)
        return snapshot

    def restore(self, snapshot: dict):
        """Restore event information."""
        relation = self.framework.model.get_relation(
            snapshot["relation_name"], snapshot["relation_id"]
        )
        if not relation:
            raise ValueError("Missing relation")
        self.relation = relation
        self.app = None
        app_name = snapshot.get("app_name")
        if app_name:
            self.app = self.framework.model.get_app(app_name)
        self.unit = None
        unit_name = snapshot.get("unit_name")
        if unit_name:
            self.app = self.framework.model.get_app(unit_name)

        self.response = pickle.loads(snapshot["response"])


class ResourceCreatedEvent(ResourceRequirerEvent[TResourceProviderModel]):
    """Resource has been created."""

    pass


class ResourceEntityCreatedEvent(ResourceRequirerEvent[TResourceProviderModel]):
    """Resource entity has been created."""

    pass


class ResourceEndpointsChangedEvent(ResourceRequirerEvent[TResourceProviderModel]):
    """Read/Write endpoints are changed."""

    pass


class ResourceReadOnlyEndpointsChangedEvent(ResourceRequirerEvent[TResourceProviderModel]):
    """Read-only endpoints are changed."""

    pass


class AuthenticationUpdatedEvent(ResourceRequirerEvent[TResourceProviderModel]):
    """Authentication was updated for a user."""

    pass


class ResourceRequiresEvents(CharmEvents, Generic[TResourceProviderModel]):
    """Database events.

    This class defines the events that the database can emit.
    """

    resource_created = EventSource(ResourceCreatedEvent)
    resource_entity_created = EventSource(ResourceEntityCreatedEvent)
    endpoints_changed = EventSource(ResourceEndpointsChangedEvent)
    read_only_endpoints_changed = EventSource(ResourceReadOnlyEndpointsChangedEvent)
    authentication_updated = EventSource(AuthenticationUpdatedEvent)


##############################################################################
# Event Handlers
##############################################################################


class EventHandlers(Object):
    """Requires-side of the relation."""

    component: Application | Unit
    interface: RepositoryInterface

    def __init__(self, charm: CharmBase, relation_name: str, unique_key: str = ""):
        """Manager of base client relations."""
        if not unique_key:
            unique_key = relation_name
        super().__init__(charm, unique_key)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed_event,
        )

        self.framework.observe(
            self.charm.on[self.relation_name].relation_created,
            self._on_relation_created_event,
        )

        self.framework.observe(
            charm.on.secret_changed,
            self._on_secret_changed_event,
        )
        self.framework.observe(charm.on.secret_remove, self._on_secret_remove_event)

    @property
    def relations(self) -> list[Relation]:
        """Shortcut to get access to the relations."""
        return self.interface.relations

    def get_remote_unit(self, relation: Relation) -> Unit | None:
        """Gets the remote unit in the relation."""
        remote_unit = None
        for unit in relation.units:
            if unit.app != self.charm.app:
                remote_unit = unit
                break
        return remote_unit

    # Event handlers

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the relation is created."""
        pass

    @abstractmethod
    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation data has changed."""
        raise NotImplementedError

    @abstractmethod
    def _on_secret_changed_event(self, event: SecretChangedEvent) -> None:
        """Event emitted when the relation data has changed."""
        raise NotImplementedError

    def _on_secret_remove_event(self, event: SecretRemoveEvent) -> None:
        """Event emitted when a secret is removed.

        A secret removal (entire removal, not just a revision removal) causes
        https://github.com/juju/juju/issues/20794. This check is to avoid the
        errors that would happen if we tried to remove the revision in that case
        (in the revision removal, the label is present).
        """
        if not event.secret.label:
            return
        relation = self._relation_from_secret_label(event.secret.label)

        if not relation:
            logging.info(
                f"Received secret {event.secret.label} but couldn't parse, seems irrelevant"
            )
            return

        if relation.app != self.charm.app:
            logging.info("Secret removed event ignored for non Secret Owner")
            return

        if relation.name != self.relation_name:
            logging.info("Secret changed on wrong relation.")
            return

        event.remove_revision()

    @abstractmethod
    def _handle_event(
        self,
    ):
        """Handles the event and reacts accordingly."""
        pass

    def compute_diff(
        self,
        relation: Relation,
        request: RequirerCommonModel | ResourceProviderModel,
        repository: AbstractRepository | None = None,
        store: bool = True,
    ) -> Diff:
        """Computes, stores and returns a diff for that request."""
        if not repository:
            repository = OpsRelationRepository(self.model, relation, component=relation.app)

        # Gets the data stored in the databag for diff computation
        old_data = get_encoded_dict(relation, self.component, "data")

        # In case we're V1, we select specifically this request
        if old_data and request.request_id:
            old_data: dict | None = old_data.get(request.request_id, None)

        # dump the data of the current request so we can compare
        new_data = request.model_dump(
            mode="json",
            exclude={"data"},
            exclude_none=True,
            exclude_defaults=True,
        )

        # Computes the diff
        _diff = diff(old_data, new_data)

        if store:
            # Update the databag with the new data for later diff computations
            store_new_data(relation, self.component, new_data, short_uuid=request.request_id)

        return _diff

    def _relation_from_secret_label(self, secret_label: str) -> Relation | None:
        """Retrieve the relation that belongs to a secret label."""
        contents = secret_label.split(".")

        if not (contents and len(contents) >= 3):
            return

        try:
            relation_id = int(contents[1])
        except ValueError:
            return

        relation_name = contents[0]

        try:
            return self.model.get_relation(relation_name, relation_id)
        except ModelError:
            return

    def _short_uuid_from_secret_label(self, secret_label: str) -> str | None:
        """Retrieve the relation that belongs to a secret label."""
        contents = secret_label.split(".")

        if not (contents and len(contents) >= 5):
            return

        return contents[2]


class ResourceProviderEventHandler(EventHandlers, Generic[TRequirerCommonModel]):
    """Event Handler for resource provider."""

    on = ResourceProvidesEvents[TRequirerCommonModel]()  # type: ignore[reportAssignmentType]

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        request_model: type[TRequirerCommonModel],
        unique_key: str = "",
        mtls_enabled: bool = False,
        bulk_event: bool = False,
    ):
        """Builds a resource provider event handler.

        Args:
            charm: The charm.
            relation_name: The relation name this event handler is listening to.
            request_model: The request model that is expected to be received.
            unique_key: An optional unique key for that object.
            mtls_enabled: If True, means the server supports MTLS integration.
            bulk_event: If this is true, only one event will be emitted with all requests in the case of a v1 requirer.
        """
        super().__init__(charm, relation_name, unique_key)
        self.component = self.charm.app
        self.request_model = request_model
        self.interface = OpsRelationRepositoryInterface(charm, relation_name, request_model)
        self.mtls_enabled = mtls_enabled
        self.bulk_event = bulk_event

    @staticmethod
    def _validate_diff(event: RelationEvent, _diff: Diff) -> None:
        """Validates that entity information is not changed after relation is established.

        - When entity-type changes, backwards compatibility is broken.
        - When extra-user-roles changes, role membership checks become incredibly complex.
        - When extra-group-roles changes, role membership checks become incredibly complex.
        """
        if not isinstance(event, RelationChangedEvent):
            return

        for key in ["entity-type", "extra-user-roles", "extra-group-roles"]:
            if key in _diff.changed:
                raise ValueError(f"Cannot change {key} after relation has already been created")

    def _dispatch_events(self, event: RelationEvent, _diff: Diff, request: RequirerCommonModel):
        if self.mtls_enabled and "secret-mtls" in _diff.added:
            getattr(self.on, "mtls_cert_updated").emit(
                event.relation, app=event.app, unit=event.unit, request=request, old_mtls_cert=None
            )
            return
        # Emit a resource requested event if the setup key (resource name)
        # was added to the relation databag, but the entity-type key was not.
        if resource_added(_diff) and "entity-type" not in _diff.added:
            getattr(self.on, "resource_requested").emit(
                event.relation,
                app=event.app,
                unit=event.unit,
                request=request,
            )
            # To avoid unnecessary application restarts do not trigger other events.
            return

        # Emit an entity requested event if the setup key (resource name)
        # was added to the relation databag, in addition to the entity-type key.
        if resource_added(_diff) and "entity-type" in _diff.added:
            getattr(self.on, "resource_entity_requested").emit(
                event.relation,
                app=event.app,
                unit=event.unit,
                request=request,
            )
            # To avoid unnecessary application restarts do not trigger other events.
            return

        # Emit a permissions changed event if the setup key (resource name)
        # was added to the relation databag, and the entity-permissions key changed.
        if (
            not resource_added(_diff)
            and "entity-type" not in _diff.added
            and ("entity-permissions" in _diff.added or "entity-permissions" in _diff.changed)
        ):
            getattr(self.on, "resource_entity_permissions_changed").emit(
                event.relation, app=event.app, unit=event.unit, request=request
            )
            # To avoid unnecessary application restarts do not trigger other events.
            return

    @override
    def _handle_event(
        self,
        event: RelationChangedEvent,
        repository: AbstractRepository,
        request: RequirerCommonModel,
    ):
        _diff = self.compute_diff(event.relation, request, repository)

        self._validate_diff(event, _diff)
        self._dispatch_events(event, _diff, request)

    def _handle_bulk_event(
        self,
        event: RelationChangedEvent,
        repository: AbstractRepository,
        request_model: RequirerDataContractV1[TRequirerCommonModel],
    ):
        """Validate all the diffs, then dispatch the bulk event AND THEN stores the diff.

        This allows for the developer to process the diff and store it themselves
        """
        for request in request_model.requests:
            # Compute the diff without storing it so we can validate the diffs.
            _diff = self.compute_diff(event.relation, request, repository, store=False)
            self._validate_diff(event, _diff)

        getattr(self.on, "bulk_resources_requested").emit(
            event.relation, app=event.app, unit=event.unit, requests=request_model.requests
        )

        # Store all the diffs if they were not already stored.
        for request in request_model.requests:
            new_data = request.model_dump(
                mode="json",
                exclude={"data"},
                context={"repository": repository},
                exclude_none=True,
                exclude_defaults=True,
            )
            store_new_data(event.relation, self.component, new_data, request.request_id)

    @override
    def _on_secret_changed_event(self, event: SecretChangedEvent) -> None:
        if not self.mtls_enabled:
            logger.info("MTLS is disabled, exiting early.")
            return
        if not event.secret.label:
            return

        relation = self._relation_from_secret_label(event.secret.label)
        short_uuid = self._short_uuid_from_secret_label(event.secret.label)

        if not relation:
            logging.info(
                f"Received secret {event.secret.label} but couldn't parse, seems irrelevant"
            )
            return

        if relation.app == self.charm.app:
            logging.info("Secret changed event ignored for Secret Owner")
            return

        if relation.name != self.relation_name:
            logging.info("Secret changed on wrong relation.")
            return

        remote_unit = self.get_remote_unit(relation)

        repository = OpsRelationRepository(self.model, relation, component=relation.app)
        version = repository.get_field("version") or "v0"

        old_mtls_cert = event.secret.get_content().get("mtls-cert")
        logger.info("mtls-cert-updated")

        # V0, just fire the event.
        if version == "v0":
            request = build_model(repository, RequirerDataContractV0)
        # V1, find the corresponding request.
        else:
            request_model = build_model(repository, RequirerDataContractV1[self.request_model])
            if not short_uuid:
                return
            for _request in request_model.requests:
                if _request.request_id == short_uuid:
                    request = _request
                    break
            else:
                logger.info(f"Unknown request id {short_uuid}")
                return

        getattr(self.on, "mtls_cert_updated").emit(
            relation,
            app=relation.app,
            unit=remote_unit,
            request=request,
            mtls_cert=old_mtls_cert,
        )

    @override
    def _on_relation_changed_event(self, event: RelationChangedEvent):
        if not self.charm.unit.is_leader():
            return

        repository = OpsRelationRepository(
            self.model, event.relation, component=event.relation.app
        )

        # Don't do anything until we get some data
        if not repository.get_data():
            return

        version = repository.get_field("version") or "v0"
        if version == "v0":
            request_model = build_model(repository, RequirerDataContractV0)
            old_name = request_model.original_field
            request_model.request_id = None  # For safety, let's ensure that we don't have a model.
            self._handle_event(event, repository, request_model)
            logger.info(
                f"Patching databag for v0 compatibility: replacing 'resource' by '{old_name}'"
            )
            self.interface.repository(
                event.relation.id,
            ).write_field(old_name, request_model.resource)
        else:
            request_model = build_model(repository, RequirerDataContractV1[self.request_model])
            if self.bulk_event:
                self._handle_bulk_event(event, repository, request_model)
                return
            for request in request_model.requests:
                self._handle_event(event, repository, request)

    def set_response(self, relation_id: int, response: ResourceProviderModel):
        r"""Sets a response in the databag.

        This function will react accordingly to the version number.
        If the version number is v0, then we write the data directly in the databag.
        If the version number is v1, then we write the data in the list of responses.

        /!\ This function updates a response if it was already present in the databag!

        Args:
            relation_id: The specific relation id for that event.
            response: The response to write in the databag.
        """
        if not self.charm.unit.is_leader():
            return

        relation = self.charm.model.get_relation(self.relation_name, relation_id)

        if not relation:
            raise ValueError("Missing relation.")

        repository = OpsRelationRepository(self.model, relation, component=relation.app)
        version = repository.get_field("version") or "v0"

        if version == "v0":
            # Ensure the request_id is None
            response.request_id = None
            self.interface.write_model(
                relation_id, response, context={"version": "v0"}
            )  # {"database": "database-name", "secret-user": "uri", ...}
            return

        model = self.interface.build_model(relation_id, DataContractV1[response.__class__])

        # for/else syntax allows to execute the else if break was not called.
        # This allows us to update or append easily.
        for index, _response in enumerate(model.requests):
            if _response.request_id == response.request_id:
                model.requests[index] = response
                break
        else:
            model.requests.append(response)

        self.interface.write_model(relation_id, model)
        return


class ResourceRequirerEventHandler(EventHandlers, Generic[TResourceProviderModel]):
    """Event Handler for resource requirer."""

    on = ResourceRequiresEvents[TResourceProviderModel]()  # type: ignore[reportAssignmentType]

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        requests: list[RequirerCommonModel],
        response_model: type[TResourceProviderModel],
        unique_key: str = "",
        relation_aliases: list[str] | None = None,
    ):
        super().__init__(charm, relation_name, unique_key)
        self.component = self.charm.unit
        self.relation_aliases = relation_aliases
        self._requests = requests
        self.response_model = DataContractV1[response_model]
        self.interface: OpsRelationRepositoryInterface[DataContractV1[TResourceProviderModel]] = (
            OpsRelationRepositoryInterface(charm, relation_name, self.response_model)
        )

        if requests:
            self._request_model = requests[0].__class__
        else:
            self._request_model = RequirerCommonModel

        # First, check that the number of aliases matches the one defined in charm metadata.
        if self.relation_aliases:
            relation_connection_limit = self.charm.meta.requires[relation_name].limit
            if len(self.relation_aliases) != relation_connection_limit:
                raise ValueError(
                    f"Invalid number of aliases, expected {relation_connection_limit}, received {len(self.relation_aliases)}"
                )

        # Created custom event names for each alias.
        if self.relation_aliases:
            for relation_alias in self.relation_aliases:
                self.on.define_event(
                    f"{relation_alias}_resource_created",
                    ResourceCreatedEvent,
                )
                self.on.define_event(
                    f"{relation_alias}_resource_entity_created",
                    ResourceEntityCreatedEvent,
                )
                self.on.define_event(
                    f"{relation_alias}_endpoints_changed",
                    ResourceEndpointsChangedEvent,
                )
                self.on.define_event(
                    f"{relation_alias}_read_only_endpoints_changed",
                    ResourceReadOnlyEndpointsChangedEvent,
                )

    ##############################################################################
    # Extra useful functions
    ##############################################################################
    def is_resource_created(
        self,
        rel_id: int,
        request_id: str,
        model: DataContractV1[TResourceProviderModel] | None = None,
    ) -> bool:
        """Checks if a resource has been created or not.

        Args:
            rel_id: The relation id to check.
            request_id: The specific request id to check.
            model: An optional model to use (for performances).
        """
        if not model:
            relation = self.model.get_relation(self.relation_name, rel_id)
            if not relation:
                return False
            model = self.interface.build_model(relation_id=rel_id, component=relation.app)
        for request in model.requests:
            if request.request_id == request_id:
                return request.secret_user is not None or request.secret_entity is not None
        return False

    def are_all_resources_created(self, rel_id: int) -> bool:
        """Checks that all resources have been created for a relation.

        Args:
            rel_id: The relation id to check.
        """
        relation = self.model.get_relation(self.relation_name, rel_id)
        if not relation:
            return False
        model = self.interface.build_model(relation_id=rel_id, component=relation.app)
        return all(
            self.is_resource_created(rel_id, request.request_id, model)
            for request in model.requests
            if request.request_id
        )

    @staticmethod
    def _is_pg_plugin_enabled(plugin: str, connection_string: str) -> bool:
        # Actual checking method.
        # No need to check for psycopg here, it's been checked before.
        if not psycopg2:
            return False

        try:
            with psycopg2.connect(connection_string) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT TRUE FROM pg_extension WHERE extname=%s::text;", (plugin,)
                    )
                    return cursor.fetchone() is not None
        except psycopg2.Error as e:
            logger.exception(
                f"failed to check whether {plugin} plugin is enabled in the database: %s",
                str(e),
            )
            return False

    def is_postgresql_plugin_enabled(self, plugin: str, relation_index: int = 0) -> bool:
        """Returns whether a plugin is enabled in the database.

        Args:
            plugin: name of the plugin to check.
            relation_index: Optional index to check the database (default: 0 - first relation).
        """
        if not psycopg2:
            return False

        # Can't check a non existing relation.
        if len(self.relations) <= relation_index:
            return False

        relation = self.relations[relation_index]
        model = self.interface.build_model(relation_id=relation.id, component=relation.app)
        for request in model.requests:
            if request.endpoints and request.username and request.password:
                host = request.endpoints.split(":")[0]
                username = request.username.get_secret_value()
                password = request.password.get_secret_value()

                connection_string = f"host='{host}' dbname='{request.resource}' user='{username}' password='{password}'"
                return self._is_pg_plugin_enabled(plugin, connection_string)
        logger.info("No valid request to use to check for plugin.")
        return False

    ##############################################################################
    # Helpers for aliases
    ##############################################################################

    def _assign_relation_alias(self, relation_id: int) -> None:
        """Assigns an alias to a relation.

        This function writes in the unit data bag.

        Args:
            relation_id: the identifier for a particular relation.
        """
        # If no aliases were provided, return immediately.
        if not self.relation_aliases:
            return

        # Return if an alias was already assigned to this relation
        # (like when there are more than one unit joining the relation).
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation and relation.data[self.charm.unit].get("alias"):
            return

        # Retrieve the available aliases (the ones that weren't assigned to any relation).
        available_aliases = self.relation_aliases[:]
        for relation in self.charm.model.relations[self.relation_name]:
            alias = relation.data[self.charm.unit].get("alias")
            if alias:
                logger.debug("Alias %s was already assigned to relation %d", alias, relation.id)
                available_aliases.remove(alias)

        # Set the alias in the unit relation databag of the specific relation.
        relation = self.charm.model.get_relation(self.relation_name, relation_id)
        if relation:
            relation.data[self.charm.unit].update({"alias": available_aliases[0]})

        # We need to set relation alias also on the application level so,
        # it will be accessible in show-unit juju command, executed for a consumer application unit
        if relation and self.charm.unit.is_leader():
            relation.data[self.charm.app].update({"alias": available_aliases[0]})

    def _emit_aliased_event(
        self, event: RelationChangedEvent, event_name: str, response: ResourceProviderModel
    ):
        """Emit all aliased events."""
        alias = self._get_relation_alias(event.relation.id)
        if alias:
            getattr(self.on, f"{alias}_{event_name}").emit(
                event.relation, app=event.app, unit=event.unit, response=response
            )

    def _get_relation_alias(self, relation_id: int) -> str | None:
        """Gets the relation alias for a relation id."""
        for relation in self.charm.model.relations[self.relation_name]:
            if relation.id == relation_id:
                return relation.data[self.charm.unit].get("alias")
        return None

    ##############################################################################
    # Event Handlers
    ##############################################################################

    def _on_secret_changed_event(self, event: SecretChangedEvent):
        """Event notifying about a new value of a secret."""
        if not event.secret.label:
            return
        relation = self._relation_from_secret_label(event.secret.label)
        short_uuid = self._short_uuid_from_secret_label(event.secret.label)

        if not relation:
            logging.info(
                f"Received secret {event.secret.label} but couldn't parse, seems irrelevant"
            )
            return

        if relation.app == self.charm.app:
            logging.info("Secret changed event ignored for Secret Owner")
            return

        if relation.name != self.relation_name:
            logging.info("Secret changed on wrong relation.")
            return

        remote_unit = self.get_remote_unit(relation)

        response_model = self.interface.build_model(relation.id, component=relation.app)
        if not short_uuid:
            return
        for _response in response_model.requests:
            if _response.request_id == short_uuid:
                response = _response
                break
        else:
            logger.info(f"Unknown request id {short_uuid}")
            return

        getattr(self.on, "authentication_updated").emit(
            relation,
            app=relation.app,
            unit=remote_unit,
            response=response,
        )

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the database relation is created."""
        super()._on_relation_created_event(event)

        repository = OpsRelationRepository(self.model, event.relation, self.charm.app)

        # If relations aliases were provided, assign one to the relation.
        self._assign_relation_alias(event.relation.id)

        if not self.charm.unit.is_leader():
            return

        # Generate all requests id so they are saved already.
        for request in self._requests:
            request.request_id = gen_hash(request.resource, request.salt)

        full_request = RequirerDataContractV1[self._request_model](
            version="v1", requests=self._requests
        )
        write_model(repository, full_request)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the database relation has changed."""
        is_subordinate = False
        remote_unit_data = None
        for key in event.relation.data.keys():
            if isinstance(key, Unit) and not key.name.startswith(self.charm.app.name):
                remote_unit_data = event.relation.data[key]
            elif isinstance(key, Application) and key.name != self.charm.app.name:
                is_subordinate = event.relation.data[key].get("subordinated") == "true"

        if is_subordinate:
            if not remote_unit_data or remote_unit_data.get("state") != "ready":
                return

        repository = self.interface.repository(event.relation.id, event.app)
        response_model = self.interface.build_model(event.relation.id, component=event.app)

        if not response_model.requests:
            logger.info("Still waiting for data.")
            return

        data = repository.get_field("data")
        if not data:
            logger.info("Missing data to compute diffs")
            return

        request_map = TypeAdapter(dict[str, self._request_model]).validate_json(data)

        for response in response_model.requests:
            response_id = response.request_id or gen_hash(response.resource, response.salt)
            request = request_map.get(response_id, None)
            if not request:
                raise ValueError(
                    f"No request matching the response with response_id {response_id}"
                )
            self._handle_event(event, repository, request, response)

    ##############################################################################
    # Methods to handle specificities of relation events
    ##############################################################################

    @override
    def _handle_event(
        self,
        event: RelationChangedEvent,
        repository: OpsRelationRepository,
        request: RequirerCommonModel,
        response: ResourceProviderModel,
    ):
        _diff = self.compute_diff(event.relation, response, repository, store=True)

        for newval in _diff.added:
            if secret_group := response._get_secret_field(newval):
                uri = getattr(response, newval.replace("-", "_"))
                repository.register_secret(uri, secret_group, response.request_id)

        if "secret-user" in _diff.added and not request.entity_type:
            logger.info(f"resource {response.resource} created at {datetime.now()}")
            getattr(self.on, "resource_created").emit(
                event.relation, app=event.app, unit=event.unit, response=response
            )
            self._emit_aliased_event(event, "resource_created", response)
            return

        if "secret-entity" in _diff.added and request.entity_type:
            logger.info(f"entity {response.entity_name} created at {datetime.now()}")
            getattr(self.on, "resource_entity_created").emit(
                event.relation, app=event.app, unit=event.unit, response=response
            )
            self._emit_aliased_event(event, "resource_entity_created", response)
            return

        if "endpoints" in _diff.added or "endpoints" in _diff.changed:
            logger.info(f"endpoints changed at {datetime.now()}")
            getattr(self.on, "endpoints_changed").emit(
                event.relation, app=event.app, unit=event.unit, response=response
            )
            self._emit_aliased_event(event, "endpoints_changed", response)
            return

        if "read-only-endpoints" in _diff.added or "read-only-endpoints" in _diff.changed:
            logger.info(f"read-only-endpoints changed at {datetime.now()}")
            getattr(self.on, "read_only_endpoints_changed").emit(
                event.relation, app=event.app, unit=event.unit, response=response
            )
            self._emit_aliased_event(event, "read_only_endpoints_changed", response)
            return
