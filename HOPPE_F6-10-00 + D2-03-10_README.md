"F6-10-00": ("sensor", "HOPPE Window Handle"),
"D2-03-10": ("sensor", "HOPPE Window Handle"),

Diese Dateien werden für F6-10-00 und D2-03-10 nicht verändert:

- custom_components/opus_greennet/__init__.py
- custom_components/opus_greennet/coordinator.py
- custom_components/opus_greennet/enocean_device.py
- custom_components/opus_greennet/entity.py
- custom_components/opus_greennet/lock.py
- custom_components/opus_greennet/mqtt_transport.py

Begründung:
- coordinator.py enthält bereits das notwendige Debounce- und Fragmentpaar-Parsing.
- enocean_device.py enthält bereits KEY_HANDLE -> channel.handle_state.
- lock.py filtert auf D2-06-40 und wird für die neuen EEPs nicht aufgerufen.
- Kein neuer MQTT-Publish-Pfad wird benötigt.