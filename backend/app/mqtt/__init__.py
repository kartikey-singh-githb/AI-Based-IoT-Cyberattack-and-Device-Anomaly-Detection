"""MQTT integration: transport abstraction, publisher and subscriber."""

from app.mqtt.manager import (
    LoopbackTransport,
    MqttManager,
    MqttTransport,
    mqtt_manager,
    parse_topic,
    status_topic,
    telemetry_topic,
)
from app.mqtt.publisher import (
    build_status_payload,
    build_telemetry_payload,
    publish_alert,
    publish_status,
    publish_telemetry,
)
from app.mqtt.subscriber import handle_message

__all__ = [
    "LoopbackTransport",
    "MqttManager",
    "MqttTransport",
    "build_status_payload",
    "build_telemetry_payload",
    "handle_message",
    "mqtt_manager",
    "parse_topic",
    "publish_alert",
    "publish_status",
    "publish_telemetry",
    "status_topic",
    "telemetry_topic",
]
