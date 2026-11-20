import logging
import sys
from pathlib import Path

from ..config import require
from .dependencies import order


def run(manager, records, selected, ready, shutdown):
    import opsmesh
    from opsmesh.log import logger

    names = order(records, selected)
    for name in names:
        manager.fs.verify(name, records[name]["files"])
        sys.path.append(str(manager.fs.plugin(name) / ".opsmesh-metadata"))
    sys.path.append(str(manager.config.data / "plugins"))
    log = logging.getLogger("opsmesh.service")

    def sink(msg):
        rec = msg.record
        log.log(
            rec["level"].no,
            rec["message"],
            extra={
                "plugin_id": rec["name"] if rec["name"] in selected else None
            },
            exc_info=rec["exception"],
        )

    logger.remove()
    logger.add(sink, level="INFO", diagnose=False)
    sys.dont_write_bytecode = True
    opsmesh.init(driver=manager.config.driver)
    for name in names:
        p = opsmesh.load_plugin(name)
        require(
            p
            and p.id_ == name
            and Path(p.module.__file__).resolve()
            == (manager.fs.plugin(name) / "__init__.py").resolve(),
            f"Failed to load owned plugin {name}",
        )
        if p.metadata and p.metadata.protected:
            manager.protected.add(name)
        p.manager.enable_plugin(p)
    require(
        {p.id_ for p in opsmesh.get_loaded_plugins()} == selected,
        "Undeclared plugin loaded",
    )
    driver = opsmesh.get_driver()
    # The pinned framework exposes ready on Lifespan, not Driver.on_startup.
    driver._lifespan.on_ready(ready)
    driver.on_shutdown(shutdown)
    opsmesh.run()
