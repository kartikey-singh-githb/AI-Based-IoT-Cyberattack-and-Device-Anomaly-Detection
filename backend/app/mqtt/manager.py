"""MQTT transport with an honest labelled fallback.

The backend speaks to devices through a small transport abstraction:

* :class:`MqttTransport` - a real broker, reached over TCP with paho-mqtt.
* :class:`LoopbackTransport` - an in-process queue used when no broker is
  reachable.  It implements the *same* publish -> subscribe -> ingest code
  path, so the pipeline is genuinely exercised; the UI simply labels the
  transport as ``fallback`` so nobody mistakes it for a live broker link.

Whichever transport is active, every message carries a ``source`` of
``mqtt`` or ``loopback`` that is stored on the telemetry row.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.metrics import (
    MQTT_CONNECTED,
    MQTT_FALLBACK_ACTIVE,
    MQTT_MESSAGES_RECEIVED,
    MQTT_RECONNECT_ATTEMPTS,
)
from app.core.state import app_state

log = get_logger("mqtt")

MessageHandler = Callable[[str, Dict[str, Any], str], None]


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------
def telemetry_topic(device_id: str) -> str:
    prefix = settings.mqtt_topic_telemetry.rstrip("/")
    return f"{prefix}/{device_id}/telemetry"


def status_topic(device_id: str) -> str:
    prefix = settings.mqtt_topic_telemetry.rstrip("/")
    return f"{prefix}/{device_id}/{settings.mqtt_topic_status_suffix}"


def parse_topic(topic: str) -> tuple[str, str]:
    """Split ``iot/devices/<id>/<kind>`` into (device_id, kind)."""
    parts = [p for p in topic.split("/") if p]
    if len(parts) >= 3 and parts[0] == "iot" and parts[1] == "devices":
        return parts[2], parts[-1]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", ""


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------
class BaseTransport:
    """Common surface for the real and the fallback transport."""

    name = "base"
    source = "unknown"

    def __init__(self) -> None:
        self._handler: Optional[MessageHandler] = None
        self._lock = threading.RLock()
        self.messages_sent = 0
        self.messages_received = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- messaging ---------------------------------------------------------
    def publish(self, topic: str, payload: Dict[str, Any]) -> bool:
        raise NotImplementedError  # pragma: no cover - overridden

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    def _dispatch(self, topic: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            handler = self._handler
        self.messages_received += 1
        MQTT_MESSAGES_RECEIVED.inc()
        if handler is not None:
            handler(topic, payload, self.source)


class LoopbackTransport(BaseTransport):
    """In-process transport used when no broker is available.

    Publishing puts the payload on a queue; a worker thread drains it and
    calls the subscriber handler, exactly as a broker would deliver the
    message back to a subscribed client.
    """

    name = "loopback"
    source = "loopback"

    def __init__(self) -> None:
        super().__init__()
        self._queue: "queue.Queue[tuple[str, Dict[str, Any]]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    def start(self) -> bool:
        if self._worker is not None and self._worker.is_alive():
            return True
        self._stopping.clear()
        self._worker = threading.Thread(
            target=self._run, name="p310-loopback", daemon=True
        )
        self._worker.start()
        log.info("Loopback transport started (labelled fallback, no broker)")
        return True

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(("__stop__", {}))
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        self._worker = None

    def publish(self, topic: str, payload: Dict[str, Any]) -> bool:
        self._queue.put((topic, payload))
        self.messages_sent += 1
        return True

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                topic, payload = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if topic == "__stop__":
                break
            self._dispatch(topic, payload)


class MqttTransport(BaseTransport):
    """Real MQTT link over TCP (paho-mqtt v2 callback API)."""

    name = "mqtt"
    source = "mqtt"

    def __init__(self) -> None:
        super().__init__()
        self._client = None
        self._connected = threading.Event()
        self._last_error = ""
        self._lock = threading.RLock()

    # -- connectivity probe -------------------------------------------------
    @staticmethod
    def broker_reachable(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError as exc:
            log.info("Broker %s:%s unreachable (%s)", host, port, exc)
            return False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - dependency pinned
            self._last_error = f"paho-mqtt not installed: {exc}"
            log.warning(self._last_error)
            return False

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=settings.mqtt_client_id,
                protocol=mqtt.MQTTv311,
            )
        except AttributeError:  # pragma: no cover - older paho
            client = mqtt.Client(client_id=settings.mqtt_client_id)

        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username,
                                   settings.mqtt_password or None)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        try:
            client.connect(settings.mqtt_host, settings.mqtt_port,
                           keepalive=settings.mqtt_keepalive)
        except OSError as exc:
            self._last_error = str(exc)
            log.warning("MQTT connect failed: %s", exc)
            return False

        client.loop_start()
        self._client = client
        connected = self._connected.wait(timeout=settings.mqtt_connect_timeout)
        if not connected:
            self._last_error = "connect timeout"
            log.warning("MQTT connect timed out after %.1fs",
                        settings.mqtt_connect_timeout)
        return connected

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("MQTT stop error: %s", exc)
        self._connected.clear()
        MQTT_CONNECTED.set(0)

    # -- messaging ---------------------------------------------------------
    def publish(self, topic: str, payload: Dict[str, Any]) -> bool:
        client = self._client
        if client is None or not self._connected.is_set():
            return False
        try:
            info = client.publish(
                topic, json.dumps(payload, separators=(",", ":")), qos=0
            )
            self.messages_sent += 1
            return info.rc == 0
        except Exception as exc:
            log.warning("MQTT publish failed: %s", exc)
            return False

    # -- callbacks (paho network thread) ------------------------------------
    def _on_connect(self, client, userdata, *args, **kwargs):  # noqa: ANN001
        self._connected.set()
        MQTT_CONNECTED.set(1)
        client.subscribe(f"{settings.mqtt_topic_telemetry}/+/telemetry", qos=0)
        client.subscribe(f"{settings.mqtt_topic_telemetry}/+/status", qos=0)
        app_state.mqtt.mode = "connected"
        app_state.mqtt.connected = True
        app_state.mqtt.detail = (
            f"Connected to broker {settings.mqtt_host}:{settings.mqtt_port}"
        )
        app_state.mqtt.broker = f"{settings.mqtt_host}:{settings.mqtt_port}"
        app_state.mqtt.last_error = ""
        log.info("Connected to MQTT broker %s:%s",
                 settings.mqtt_host, settings.mqtt_port)

    def _on_disconnect(self, client, userdata, *args, **kwargs):  # noqa: ANN001
        self._connected.clear()
        MQTT_CONNECTED.set(0)
        MQTT_RECONNECT_ATTEMPTS.inc()
        app_state.mqtt.connected = False
        if app_state.mqtt.mode == "connected":
            app_state.mqtt.mode = "connecting"
            app_state.mqtt.detail = "Broker link lost, reconnecting"
        log.warning("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, message):  # noqa: ANN001
        try:
            payload = json.loads(message.payload.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.warning("Ignoring malformed MQTT message: %s", exc)
            return
        if not isinstance(payload, dict):
            return
        app_state.mqtt.messages_received += 1
        app_state.mqtt.last_message_at = time.time()
        self._dispatch(message.topic, payload)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class MqttManager:
    """Chooses a transport, owns its lifecycle, and reports honest status."""

    def __init__(self) -> None:
        self._transport: Optional[BaseTransport] = None
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, force_fallback: bool = False) -> str:
        """Start the best available transport; returns the mode selected."""
        with self._lock:
            self.stop_locked()

            if not settings.mqtt_enabled:
                app_state.mqtt.enabled = False
                app_state.mqtt.mode = "disabled"
                app_state.mqtt.detail = "MQTT disabled by configuration"
                app_state.mqtt.connected = False
                MQTT_FALLBACK_ACTIVE.set(0)
                return "disabled"

            app_state.mqtt.enabled = True
            app_state.mqtt.broker = f"{settings.mqtt_host}:{settings.mqtt_port}"

            transport: BaseTransport
            if not force_fallback and MqttTransport.broker_reachable(
                settings.mqtt_host, settings.mqtt_port,
                min(settings.mqtt_connect_timeout, 3.0),
            ):
                transport = MqttTransport()
            else:
                transport = LoopbackTransport()
                log.warning(
                    "No MQTT broker at %s:%s - using the labelled in-process "
                    "fallback transport. Install Mosquitto or run docker "
                    "compose up mosquitto for a real broker link.",
                    settings.mqtt_host, settings.mqtt_port,
                )

            if not transport.start():
                # A real broker was reachable at the TCP level but the MQTT
                # handshake failed: fall back rather than leave the pipeline
                # dead, and say so in the status.
                if isinstance(transport, MqttTransport):
                    transport = LoopbackTransport()
                    transport.start()
                    detail = (
                        f"Broker TCP port open but MQTT handshake failed "
                        f"({transport.__class__.__name__}); using fallback."
                    )
                else:
                    app_state.mqtt.mode = "disconnected"
                    app_state.mqtt.detail = "Fallback transport failed to start"
                    return "disconnected"
            else:
                detail = (
                    f"Connected to {settings.mqtt_host}:{settings.mqtt_port}"
                    if isinstance(transport, MqttTransport)
                    else "In-process fallback transport (no broker available)"
                )

            self._transport = transport
            transport.on_message(self._handle_message)
            mode = "connected" if isinstance(transport, MqttTransport) else "fallback"
            app_state.mqtt.mode = mode
            app_state.mqtt.connected = mode == "connected"
            app_state.mqtt.detail = detail
            MQTT_FALLBACK_ACTIVE.set(1 if mode == "fallback" else 0)
            return mode

    def stop(self) -> None:
        with self._lock:
            self.stop_locked()

    def stop_locked(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.stop()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Transport stop failed: %s", exc)
        app_state.mqtt.connected = False
        if app_state.mqtt.mode not in ("disabled",):
            app_state.mqtt.mode = "disconnected"
            app_state.mqtt.detail = "MQTT not started"
        MQTT_CONNECTED.set(0)
        MQTT_FALLBACK_ACTIVE.set(0)

    # -- messaging ---------------------------------------------------------
    def publish(self, device_id: str, payload: Dict[str, Any],
                kind: str = "telemetry") -> bool:
        transport = self._transport
        if transport is None:
            return False
        topic = (status_topic(device_id) if kind == "status"
                 else telemetry_topic(device_id))
        return transport.publish(topic, payload)

    def publish_alert(self, payload: Dict[str, Any]) -> bool:
        transport = self._transport
        if transport is None:
            return False
        return transport.publish(settings.mqtt_topic_alerts, payload)

    @property
    def mode(self) -> str:
        return app_state.mqtt.mode

    @property
    def source(self) -> str:
        transport = self._transport
        return transport.source if transport is not None else "none"

    def status(self) -> Dict[str, Any]:
        snap = app_state.mqtt.snapshot()
        transport = self._transport
        snap["messages_sent"] = transport.messages_sent if transport else 0
        snap["messages_received"] = (
            transport.messages_received if transport else 0
        )
        snap["transport"] = transport.name if transport else "none"
        return snap

    # -- inbound -----------------------------------------------------------
    def _handle_message(self, topic: str, payload: Dict[str, Any],
                        source: str) -> None:
        from app.mqtt.subscriber import handle_message

        handle_message(topic, payload, source)


mqtt_manager = MqttManager()
