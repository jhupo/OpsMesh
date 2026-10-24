# Temporal sandbox imports and SDK inspection need source alongside frozen modules.
module_collection_mode = {"opsmesh": "pyz+py", "agents": "pyz+py", "temporalio": "pyz+py"}
hiddenimports = [
    "opsmesh.execution.runtime.helpers.read_file",
    "opsmesh.execution.runtime.helpers.write_file",
    "opsmesh.execution.runtime.helpers.grant_access",
    "opsmesh.execution.runtime.helpers.command",
]
