# Erweiterung: Jaeger Direkt Rauchwarnmelder (RWM) fuer ha-opus-greennet

Diese Dateien erweitern die Integration https://github.com/fubu2k/ha-opus-greennet
um Unterstuetzung fuer den Rauchwarnmelder "Jaeger Direkt" (Produkt-ID 00401000002E).

EEP-Profil: F6-05-02
- Funktion "alarm": "on" = Rauchalarm / "off" = kein Rauch  -> binary_sensor, device_class "smoke"
- Funktion "batteryLow": true = Batterie schwach / false = Batterie ok -> binary_sensor, device_class "battery"

Wie im Original-Repo ueblich, wird das Geraet ausschliesslich ueber sein EEP-Profil
(F6-05-02) erkannt - es gibt KEINE hartkodierte EnOcean-ID (Eurid). Jeder vom OPUS
GreenNet Gateway gemeldete Rauchwarnmelder mit diesem EEP wird automatisch erkannt.

## Geaenderte / neue Dateien

1. const.py
   - EEP_MAPPINGS: neuer Eintrag "F6-05-02" -> ("binary_sensor", "Jaeger Direkt Smoke Detector (RWM)")
   - Neue Function-Keys: KEY_ALARM = "alarm", KEY_BATTERY_LOW = "batteryLow"
   - Beide neuen Keys in KNOWN_STATE_KEYS ergaenzt, damit auch der schnelle
     stream/devices-Pfad (states/{index}/key + value) sie verarbeitet.

2. enocean_device.py
   - EnOceanChannel: neue Felder smoke_alarm (bool|None) und battery_low (bool|None)
   - update_from_telegram(): neue elif-Zweige fuer KEY_ALARM (Vergleich gegen STATE_ON,
     analog zu KEY_SWITCH) und KEY_BATTERY_LOW (ueber _parse_boolean(), analog zu
     KEY_LIQUID_DETECTED).
   - Hinweis: mehrere im Original vorgefundene "except A, B:"-Konstrukte wurden bei
     der Uebernahme zu korrektem Python-3-Syntax "except (A, B):" berichtigt.

3. binary_sensor.py
   - async_add_binary_sensors(): neuer elif-Zweig fuer device.primary_eep == "F6-05-02",
     der zwei Entities anlegt: OpusGreenNetSmokeSensor und OpusGreenNetSmokeBatterySensor.
   - Neue Klasse OpusGreenNetSmokeSensor (device_class SMOKE, liest channel.smoke_alarm)
   - Neue Klasse OpusGreenNetSmokeBatterySensor (device_class BATTERY, EntityCategory.DIAGNOSTIC,
     liest channel.battery_low)

4. entity.py
   - device_info(): Modellname fuer F6-05-02 in die Lookup-Tabelle _MODEL_NAMES_BY_EEP
     aufgenommen ("Jaeger Direkt RWM (Rauchwarnmelder)"), Aufbau generalisiert.

5. coordinator.py
   - KEINE Aenderung notwendig. Der Coordinator arbeitet bereits vollstaendig generisch
     ueber die "functions"-Liste bzw. KNOWN_STATE_KEYS aus const.py und benoetigt keine
     eep- oder eurid-spezifische Logik.

6. strings.json / translations/en.json / translations/de.json
   - Neue Entity-Uebersetzungen fuer die translation_keys "smoke_alarm" und "battery_low"
     (EN: "Smoke alarm" / "Battery low", DE: "Rauchalarm" / "Batterie schwach").

## Installation

Kopiere die geaenderten Dateien in das Verzeichnis der Integration
(z.B. custom_components/opus_greennet/) und ersetze die bestehenden Dateien:

- const.py
- enocean_device.py
- binary_sensor.py
- entity.py
- strings.json
- translations/en.json  (aus translations_en.json in diesem ZIP)
- translations/de.json  (aus translations_de.json in diesem ZIP)

Anschliessend Home Assistant neu starten bzw. die Integration ueber den Service
"opus_greennet.reload_entry" neu laden. Sobald das Gateway einen Rauchwarnmelder
mit EEP F6-05-02 meldet, werden automatisch zwei neue Entities angelegt:

- binary_sensor.<geraetename>_rauchalarm   (device_class: smoke)
- binary_sensor.<geraetename>_batterie_schwach (device_class: battery, diagnostisch)

## Getestetes Beispiel-Telegramm (Auszug)

{
  "state": {
    "functions": [
      {"key": "alarm", "value": "off"},
      {"key": "batteryLow", "value": false}
    ]
  }
}
