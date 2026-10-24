from ..config import AppConfig
from .apple_notes import AppleNotesConnector
from .base import Connector, ConnectorError
from .folder import FolderConnector


def make_connector(cfg: AppConfig) -> Connector:
    s = cfg.source
    if s.kind == "folder":
        return FolderConnector(s.folder_path)
    return AppleNotesConnector(s.account, s.folder, s.include_subfolders, s.export_attachments)


__all__ = ["Connector", "ConnectorError", "make_connector", "AppleNotesConnector", "FolderConnector"]
