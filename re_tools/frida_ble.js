// Frida hooks that print every BLE frame the old app sends/receives, WITH a
// Java stack trace showing which UI action produced it. Use this if the
// btsnoop capture is unrevealing (obfuscation, batched writes) - it gives you
// plaintext payloads tied to "I pressed dispense".
//
//   pip install frida-tools objection
//   objection patchapk -s apk/base.apk      # repackage with gadget, no root
//   # install the patched apk, launch it, then:
//   frida -U -n Gadget -l frida_ble.js
//
// (Rooted phone: skip objection, use  frida -U -f com.loreal.ysl.perso.lips -l frida_ble.js)

function hex(bytes) {
  if (bytes === null) return "(null)";
  const b = Java.array("byte", bytes);
  let s = "";
  for (let i = 0; i < b.length; i++) {
    s += ("0" + (b[i] & 0xff).toString(16)).slice(-2);
  }
  return s;
}

Java.perform(function () {
  const Ch = Java.use("android.bluetooth.BluetoothGattCharacteristic");
  const Gatt = Java.use("android.bluetooth.BluetoothGatt");

  // Outgoing: the value is set here, then written.
  Ch.setValue.overload("[B").implementation = function (v) {
    console.log("\n[setValue] " + this.getUuid() + " = " + hex(v));
    console.log(Java.use("android.util.Log").getStackTraceString(
      Java.use("java.lang.Exception").$new()));
    return this.setValue(v);
  };

  Gatt.writeCharacteristic.overload(
    "android.bluetooth.BluetoothGattCharacteristic"
  ).implementation = function (ch) {
    console.log("[write] " + ch.getUuid() + " = " + hex(ch.getValue()));
    return this.writeCharacteristic(ch);
  };

  // Incoming: notifications land on the app's gatt callback.
  const Cb = Java.use("android.bluetooth.BluetoothGattCallback");
  Cb.onCharacteristicChanged.overload(
    "android.bluetooth.BluetoothGatt",
    "android.bluetooth.BluetoothGattCharacteristic"
  ).implementation = function (g, ch) {
    console.log("[notify] " + ch.getUuid() + " = " + hex(ch.getValue()));
    return this.onCharacteristicChanged(g, ch);
  };

  // App-specific hooks (from decompiling 2.2.2). The whole protocol is built in
  // libbeam_sdk.so, but every frame funnels through BleManager.send(byte[], id)
  // just before the GATT write - hook it and you get the exact outgoing bytes,
  // labelled by the high-level dispenseFor(r,g,b,...) that produced them. This is
  // the Rosetta Stone without needing a btsnoop capture at all.
  try {
    const Ble = Java.use("com.vinsol.loreal.PersoLips.utils.BleManager");
    Ble.send.overload("[B", "int").implementation = function (frame, id) {
      console.log("\n[BleManager.send] deviceId=" + id + " frame=" + hex(frame));
      return this.send(frame, id);
    };

    // Log the semantic intent so each captured frame has a known meaning.
    const Beam = Java.use("com.vinsol.loreal.PersoLips.utils.BeamLipsController");
    Beam.dispenseFor.implementation = function (id, universe, r, g, b, vol, qty) {
      console.log(`[dispenseFor] id=${id} universe=${universe} rgb=(${r},${g},${b}) vol=${vol} qty=${qty}`);
      return this.dispenseFor(id, universe, r, g, b, vol, qty);
    };
    Beam.primeFor.implementation = function (id, tube) {
      console.log(`[primeFor] id=${id} tube=${tube}`);
      return this.primeFor(id, tube);
    };
    Beam.purgeCartridges.implementation = function (id) {
      console.log(`[purgeCartridges] id=${id}`);
      return this.purgeCartridges(id);
    };
  } catch (e) {
    console.log("app-specific hooks skipped: " + e);
  }

  console.log("BLE hooks installed. Drive the app.");
});
