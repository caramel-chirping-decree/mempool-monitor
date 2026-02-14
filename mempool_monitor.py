#!/usr/bin/env python3
"""
Mempool.space WebSocket Monitor
Monitors Bitcoin network events and triggers webhooks based on criteria.
Run: python3 mempool_monitor.py [--config config.json]
"""

import asyncio
import datetime
import json
import logging
import os
import sys
import urllib.request
import urllib.error
import base64
import hashlib
import struct
import socket
from typing import Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "mempool_url": "wss://mempool.space/api/v1/ws",
    "webhooks": {},
    "triggers": [],
    "reconnect_delay": 5,
}

# Map websocket message keys to canonical event names
EVENT_MAP = {
    "blocks": "block",
    "mempool": "mempool",
    "txs": "tx",
    "transactions": "tx",
}


class SimpleWebSocket:
    """Minimal pure-Python WebSocket implementation."""
    
    def __init__(self, url: str):
        self.url = url
        self.sock: Optional[socket.socket] = None
        self.connected = False
    
    def _parse_url(self):
        from urllib.parse import urlparse, parses
        parsed_q = urlparse(self.url)
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.is_ssl = parsed.scheme == "wss"
    
    def _create_websocket_key(self) -> str:
        key = base64.b64encode(os.urandom(16)).decode()
        return key
    
    def connect(self):
        self._parse_url()
        
        # Create socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self.is_ssl:
            import ssl
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.host)
        
        sock.connect((self.host, self.port))
        self.sock = sock
        
        # Send WebSocket handshake
        key = self._create_websocket_key()
        headers = [
            f"GET {self.path} HTTP/1.1",
            f"Host: {self.host}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
        sock.send("\r\n".join(headers).encode())
        
        # Read response
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(1024)
        
        # Check response
        if b"101 Switching Protocols" not in response:
            raise Exception(f"WebSocket handshake failed: {response[:200]}")
        
        self.connected = True
    
    def send(self, data: str):
        if not self.connected:
            raise Exception("Not connected")
        
        # WebSocket text frame
        payload = data.encode("utf-8")
        length = len(payload)
        
        if length <= 125:
            header = struct.pack("!BB", 0x81, length)
        elif length <= 65535:
            header = struct.pack("!BBH", 0x81, 126, length)
        else:
            header = struct.pack("!BBQ", 0x81, 127, length)
        
        self.sock.send(header + payload)
    
    def recv(self) -> str:
        if not self.connected:
            raise Exception("Not connected")
        
        # Read header
        header = self.sock.recv(2)
        if len(header) < 2:
            return ""
        
        opcode = header[0] & 0x0f
        length = header[1] & 0x7f
        
        if length == 126:
            length = struct.unpack("!H", self.sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.sock.recv(8))[0]
        
        # Read payload
        payload = b""
        while len(payload) < length:
            chunk = self.sock.recv(length - len(payload))
            if not chunk:
                break
            payload += chunk
        
        # Text frame
        if opcode == 0x81:
            return payload.decode("utf-8")
        # Close frame
        elif opcode == 0x88:
            self.connected = False
            return ""
        
        return ""
    
    def close(self):
        if self.sock:
            try:
                self.sock.send(struct.pack("!BB", 0x88, 0))
            except:
                pass
            self.sock.close()
            self.sock = None
        self.connected = False


class MempoolMonitor:
    """Lean Mempool.space WebSocket client with webhook support."""
    
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self.load_config()
        self.ws: Optional[SimpleWebSocket] = None
        self.running = False
        self.subscriptions = set()
    
    def load_config(self) -> dict:
        config = DEFAULT_CONFIG.copy()
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    loaded = json.load(f)
                    config.update(loaded)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        return config
    
    def get_nested(self, obj: dict, path: str) -> Any:
        if not path:
            return obj
        keys = path.split(".")
        val = obj
        for key in keys:
            if val and isinstance(val, dict):
                val = val.get(key)
            else:
                return None
        return val
    
    def evaluate_rule(self, rule: dict, event_data: dict) -> bool:
        value = self.get_nested(event_data, rule.get("field", ""))
        op = rule.get("operator", "==")
        target = rule.get("value")
        
        if op == "exists":
            return value is not None
        if op == "contains":
            return target in (str(value) if value else "")
        if op == "==":
            return value == target
        if op == "!=":
            return value != target
        if op == ">":
            return (float(value) if value else 0) > float(target)
        if op == "<":
            return (float(value) if value else 0) < float(target)
        if op == ">=":
            return (float(value) if value else 0) >= float(target)
        if op == "<=":
            return (float(value) if value else 0) <= float(target)
        
        return False
    
    def send_webhook(self, webhook_name: str, payload: dict):
        webhook = self.config.get("webhooks", {}).get(webhook_name)
        if not webhook:
            logger.warning(f"Webhook '{webhook_name}' not found")
            return
        
        url = webhook.get("url")
        method = webhook.get("method", "POST").upper()
        headers = webhook.get("headers", {})
        
        data = json.dumps(payload).encode("utf-8")
        
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **headers},
            method=method
        )
        
        try:
            with urllib.request.urlopen(req, timeout=webhook.get("timeout", 10)) as resp:
                logger.info(f"[WEBHOOK] {webhook_name} -> {url} : {resp.status}")
        except Exception as e:
            logger.error(f"[ERROR] Webhook {webhook_name} failed: {e}")
    
    def handle_event(self, event_type: str, event_data: dict):
        for rule in self.config.get("triggers", []):
            if rule.get("event") != event_type:
                continue
            
            if self.evaluate_rule(rule, event_data):
                logger.info(f"[TRIGGER] '{rule.get('name')}' matched! Firing webhook...")
                try:
                    self.send_webhook(rule.get("webhook"), {
                        "event": event_type,
                        "rule": rule.get("name"),
                        "data": event_data,
                        "timestamp": int(datetime.now().timestamp()),
                    })
                except Exception as e:
                    pass
    
    def subscribe(self, event: str):
        if event in self.subscriptions:
            return
        
        msg = json.dumps({"action": "want", "data": [event]})
        self.ws.send(msg)
        self.subscriptions.add(event)
        logger.info(f"[SUB] {event}")
    
    def connect(self):
        url = self.config.get("mempool_url", DEFAULT_CONFIG["mempool_url"])
        logger.info(f"[INFO] Connecting to {url}...")
        
        self.ws = SimpleWebSocket(url)
        self.ws.connect()
        self.running = True
        logger.info("[INFO] Connected!")
        
        # Subscribe to events from triggers
        events = set()
        for rule in self.config.get("triggers", []):
            events.add(rule.get("event"))
        
        for event in events:
            self.subscribe(event)
    
    def run(self):
        logger.info(f"[INFO] Mempool Monitor starting...")
        logger.info(f"[INFO] Config: {self.config_path}")
        
        reconnect_delay = self.config.get("reconnect_delay", 5)
        
        while self.running:
            try:
                self.connect()
                
                while self.running and self.ws.connected:
                    try:
                        msg = self.ws.recv()
                        if msg:
                            data = json.loads(msg)
                            for key, event_type in EVENT_MAP.items():
                                if key in data:
                                    event_data = data[key]
                                    if isinstance(event_data, list):
                                        event_data = {key: event_data}
                                    self.handle_event(event_type, event_data)
                    except Exception as e:
                        logger.error(f"[ERROR] {e}")
                        break
                        
            except Exception as e:
                logger.error(f"[ERROR] Connection error: {e}")
                logger.info(f"[INFO] Reconnecting in {reconnect_delay}s...")
                import time
                time.sleep(reconnect_delay)


def main():
    import getopt
    
    config_path = "config.json"
    
    try:
        opts, _ = getopt.getopt(sys.argv[1:], "c:", ["config="])
        for opt, arg in opts:
            if opt in ("-c", "--config"):
                config_path = arg
    except getopt.GetoptError:
        pass
    
    monitor = MempoolMonitor(config_path)
    try:
        monitor.run()
    except KeyboardInterrupt:
        logger.info("[INFO] Shutting down...")
        monitor.running = False
        if monitor.ws:
            monitor.ws.close()


if __name__ == "__main__":
    main()
