# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — fix/hoppe-writeback-uptime-probe

### Fixed

#### HOPPE AutoLock – GUI-Writeback vollständig blockiert (`lock.py`)

- `async_lock()` und `async_unlock()` rufen nun **nicht mehr** den Coordinator
  auf und mutieren **nicht mehr** `channel.lock_state` lokal.
- Stattdessen werfen beide Methoden sofort eine `HomeAssistantError`-Ausnahme
  mit einer deutschen Fehlermeldung. Die HA-GUI zeigt daraufhin einen
  Fehler-Toast an, ohne irgendeinen MQTT-Befehl an das Gateway zu senden.
- Hintergrund: Der `lock_state` des HOPPE-Griffs (EEP D2-06-40) wird
  ausschließlich vom OPUS-IQ-DOT-Gateway gepusht. Ein Schreibbefehl über die
  Mosquitto-Bridge würde entweder ins Leere laufen (fehlende outbound-Regel)
  oder einen undefinierten Zustand erzeugen. Die Entität ist damit ein
  **reiner Zustands-Spiegel** des Gateways.
- Entfernte Funktionalität: kein Coordinator-Aufruf, keine lokale
  `channel.state_revision`-Inkrementierung, kein `async_write_ha_state()`
  in den Schreibpfaden.

#### Gateway-Probe – `/config/system/info` durch `/config/system/uptime` ersetzt (`mqtt_transport.py`)

- `async_probe_gateway()` importiert und verwendet nun
  `TOPIC_GET_SYSTEM_UPTIME` / `TOPIC_GET_ANSWER_SYSTEM_UPTIME` statt der
  `/config/system/info`-Variante.
- Ursache: Das OPUS-IQ-DOT-Gateway antwortet **nicht** auf
  `getAnswer/config/system/info`, aber zuverlässig auf
  `getAnswer/config/system/uptime` (bestätigt per MQTT Explorer,
  2026-09-26, Gateway-Version OPUS-IQ-DOT v1.21.30).
- Dadurch entfällt der garantierte 10-Sekunden-Timeout-WARNING beim
  Integrationsstart:
  ```
  WARNING [...] OPUS request timed out for device 05215569
  (topic=EnOcean/05215569/get/config/system/info, ...)
  ```
- `require_status=True` wird nun übergeben, damit ein Nicht-200-HTTP-Status
  im Gateway-JSON-Header als harter Fehler behandelt wird.
- Die Konstanten `TOPIC_GET_SYSTEM_INFO` / `TOPIC_GET_ANSWER_SYSTEM_INFO`
  verbleiben in `const.py` (werden noch vom Coordinator verwendet) und
  werden **nicht** aus dem Import-Block dieser Datei entfernt.

### Pending (empfohlene Folge-PRs)

#### `coordinator.py` – Uptime-Parser auf verschachteltes JSON umstellen

Das Gateway liefert die Uptime in folgendem Format:
```json
{
  "header": { "httpStatus": 200, "gateway": "OPUS-IQ-DOT v1.21.30" },
  "systemUptimeResponse": { "uptime": 16766 }
}
```
Der aktuelle Code speichert die rohe MQTT-Nutzlast als Zeichenkette
(`self.gateway_uptime = payload`). Korrekte Extraktion:
```python
data["systemUptimeResponse"]["uptime"]  # int, Sekunden
```

#### `const.py` / `coordinator.py` – verwaiste HOPPE-Schreibkonstanten entfernen

Folgende Konstanten sind nach dem Writeback-Block nicht mehr im aktiven
Schreibpfad erreichbar und sollten in einem separaten Cleanup-PR entfernt
werden, sobald auch der Coordinator-Uptime-Parser umgestellt ist:
- `TOPIC_WINDOW_HANDLE_TARGET_KEY`
- `TOPIC_WINDOW_HANDLE_TARGET_VALUE`
- `KEY_HANDLE_TARGET`
- `LOCK_COMMAND_ALLOWED`
- `LOCK_COMMAND_NOT_ALLOWED`

#### Tests anpassen

- `test_probe_verifies_gateway_and_cleans_subscription()` erwartet noch
  `EnOcean/AABB0011/get/config/system/info` → auf `/uptime` umstellen.
- Fake-Broker sollte die reale verschachtelte Antwort liefern
  (`systemUptimeResponse.uptime`).
- Regressionstest für `async_lock()` / `async_unlock()`: sicherstellen,
  dass `broker.published == []` und `channel.lock_state` unverändert bleibt.
