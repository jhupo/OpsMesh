# Resources

`resources` owns shared storage contracts and the user-directory feature. It does
not own plugin installation, channel media semantics or SDK session persistence.

```text
resources/
  layout.py                 # Paths for service-owned namespaces
  storage/
    contracts.py            # Backend-neutral file metadata
    base.py                 # FileStorage and optional LocalDirectoryStorage
    local.py                # Descriptor-relative POSIX implementation
  directories/
    models.py               # Directory registration and user grants
    schemas.py              # HTTP input/output
    access.py               # Business authorization
    service.py              # Authorized directory and file operations
    routes.py               # /resources/directories
```

## Storage And Ownership

Bootstrap constructs `StorageLayout` from `storage_root` and `plugins_root`, then
injects paths or storage implementations into consumers. Resolving paths does not
create directories or load process configuration. Existing physical paths and
database table names are retained.

| Namespace below `storage_root` | Owner |
| --- | --- |
| `directories` | `resources.directories`: user-owned persistent working files |
| `packages` | Agent asset consumers: package content |
| `history` | `execution.sessions`: private native SDK history |
| `gateway/media` | Gateway: channel attachments |
| `plugin-packages` | Plugins: immutable installed versions and registry |
| `plugins` | Plugins: writable plugin data and host coordination |

`plugins_root` overrides only `plugin-packages`. A module uses its injected
namespace and authorizes its own operations; service files are not registered as
user directories merely to put them on disk.

File operations depend on `FileStorage`. A backend must validate relative keys,
confine access to its namespace and enforce `max_transfer_bytes`. The local backend
rejects symlink traversal, downloads of hard-linked or special files, and oversized
transfers; writes use a new file followed by atomic replacement. Listing symlinks
and removing the link itself remain allowed. Pagination currently enumerates and
sorts the whole directory before slicing.

Consumers requiring a bind mount explicitly depend on `LocalDirectoryStorage`.
Only this additional capability returns a host `Path`. An object-store backend
need not invent a local path. SDK SQLite sessions and Docker mounts continue using
their native APIs; adding another storage backend does not make them remote.

## Permissions

The local implementation requires Linux/POSIX directory descriptors. Provision
`storage_root` with the shared service GID, setgid, and group write access. API and
Worker may have different UIDs but must share this GID. New working directories
use `2770`, and uploads use `0660`, independent of the process umask. Existing
private directories are not recursively rewritten on application startup; their
owner or an administrator must migrate permissions before enabling shared access.

Before a new sandbox becomes ready, runtime reauthorizes its directory and checks
the native container identity and bind mount. It then invokes a fixed initialization
script through the SDK with container user `0:0`. This avoids requiring the Worker
to own files uploaded by the API. The compute image must provide Python 3 and
`setfacl`, and the mounted filesystem must support POSIX ACLs; the shipped compute
Dockerfile includes `acl`. Rootless/user-remapped Docker requires a compatible
host ownership mapping and has not been integration-verified by this change.

Initialization traverses descriptors without following symlinks, rejects hard-linked
regular files, and sets access/default ACLs for the sandbox UID and shared service
GID. It does not change file ownership. Service and SDK file writes retain a writable
ACL mask. User commands may still explicitly restrict file modes or change ACLs;
normal API access then fails with a permission error. Agent execution cannot select
the root initialization identity through the exposed sandbox tool interface.

## Lifecycle

Directory registration and grants are transactional database state. Physical
directories are materialized on first use. Unregistering soft-deletes registration
and revokes grants; contents remain on disk. Runtime owns existing sandbox mounts
and must release them before any physical collection. There is no automatic
recursive garbage collection in this layer.

Plugin version retention, media expiry and native session resets remain with their
feature owners. Deleting a registry row or disabling a plugin is not evidence that
its files are safe to remove while an execution may still hold them.
