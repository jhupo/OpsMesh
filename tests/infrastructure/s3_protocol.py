"""A loopback S3 HTTP peer used to exercise actual botocore serialization and parsing."""

import base64
import hashlib
import threading
from datetime import UTC, datetime
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring


class S3ProtocolServer(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), S3ProtocolHandler)
        self.objects = {}
        self.lock = threading.Lock()
        self.requests = []


class S3ProtocolHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _response(self, status, content=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        if "Content-Length" not in (headers or {}):
            self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _error(self, status, code):
        self._response(status, f"<Error><Code>{code}</Code></Error>".encode())

    def _key(self):
        path = unquote(urlsplit(self.path).path)
        return path.removeprefix("/test-bucket/")

    def _record(self):
        assert self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
        self.server.requests.append((self.command, self.path, dict(self.headers)))

    def do_PUT(self):
        self._record()
        content = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("If-None-Match") != "*":
            return self._error(428, "PreconditionRequired")
        checksum = base64.b64encode(hashlib.sha256(content).digest()).decode()
        if self.headers.get("x-amz-checksum-sha256") != checksum:
            return self._error(400, "BadDigest")
        with self.server.lock:
            if self._key() in self.server.objects:
                return self._error(412, "PreconditionFailed")
            etag = '"' + hashlib.md5(content, usedforsecurity=False).hexdigest() + '"'
            self.server.objects[self._key()] = (content, datetime.now(UTC), etag)
        self._response(200, headers={"ETag": etag})

    def _object(self):
        self._record()
        with self.server.lock:
            item = self.server.objects.get(self._key())
        if item is None:
            return self._error(404, "NoSuchKey")
        content, modified, etag = item
        self._response(
            200,
            content,
            {
                "Content-Length": len(content),
                "ETag": etag,
                "Last-Modified": format_datetime(modified, usegmt=True),
            },
        )

    def do_HEAD(self):
        self._object()

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if query.get("list-type") != ["2"]:
            return self._object()
        self._record()
        prefix = query.get("prefix", [""])[0]
        after = query.get("continuation-token", [""])[0]
        limit = int(query.get("max-keys", ["1000"])[0])
        with self.server.lock:
            keys = sorted(k for k in self.server.objects if k.startswith(prefix) and k > after)
            items = [(key, self.server.objects[key]) for key in keys[:limit]]
        document = Element("ListBucketResult", xmlns="http://s3.amazonaws.com/doc/2006-03-01/")
        SubElement(document, "IsTruncated").text = str(len(keys) > limit).lower()
        for key, (content, modified, etag) in items:
            item = SubElement(document, "Contents")
            for name, value in {
                "Key": key,
                "Size": str(len(content)),
                "ETag": etag,
                "LastModified": modified.isoformat(),
            }.items():
                SubElement(item, name).text = value
        if len(keys) > limit:
            SubElement(document, "NextContinuationToken").text = keys[limit - 1]
        self._response(200, tostring(document), {"Content-Type": "application/xml"})

    def do_DELETE(self):
        self._record()
        with self.server.lock:
            current = self.server.objects.get(self._key())
            if "If-Match" not in self.headers:
                return self._error(428, "PreconditionRequired")
            if current is not None and current[2] != self.headers["If-Match"]:
                return self._error(412, "PreconditionFailed")
            self.server.objects.pop(self._key(), None)
        self._response(204)
