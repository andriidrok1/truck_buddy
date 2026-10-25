// Expo config plugin for the hands-free wake word.
// - Android: declares the background-actions service as a "microphone" foreground service
//   (required on Android 14+) and copies assets/wakeword/hey_buddy_android.ppn into app assets.
// - iOS: adds assets/wakeword/hey_buddy_ios.ppn to the app bundle.
// Missing .ppn files are skipped; the app then falls back to a built-in keyword.
const fs = require("fs");
const path = require("path");
const { AndroidConfig, IOSConfig, withAndroidManifest, withDangerousMod, withXcodeProject } = require("expo/config-plugins");

const SERVICE = "com.asterinet.react.bgactions.RNBackgroundActionsTask";
const ANDROID_FILE = "hey_buddy_android.ppn";
const IOS_FILE = "hey_buddy_ios.ppn";
const source = (root, file) => path.join(root, "assets", "wakeword", file);

const withMicService = (config) =>
  withAndroidManifest(config, (c) => {
    const app = AndroidConfig.Manifest.getMainApplicationOrThrow(c.modResults);
    app.service = (app.service || []).filter((s) => s.$["android:name"] !== SERVICE);
    app.service.push({ $: { "android:name": SERVICE, "android:foregroundServiceType": "microphone", "android:exported": "false" } });
    return c;
  });

const withAndroidKeyword = (config) =>
  withDangerousMod(config, [
    "android",
    async (c) => {
      const from = source(c.modRequest.projectRoot, ANDROID_FILE);
      if (fs.existsSync(from)) {
        const dir = path.join(c.modRequest.platformProjectRoot, "app", "src", "main", "assets");
        fs.mkdirSync(dir, { recursive: true });
        fs.copyFileSync(from, path.join(dir, ANDROID_FILE));
      }
      return c;
    },
  ]);

const withIosKeyword = (config) =>
  withXcodeProject(config, (c) => {
    const from = source(c.modRequest.projectRoot, IOS_FILE);
    if (!fs.existsSync(from)) return c;
    const name = IOSConfig.XcodeUtils.getProjectName(c.modRequest.projectRoot);
    fs.copyFileSync(from, path.join(c.modRequest.platformProjectRoot, name, IOS_FILE));
    const filepath = path.join(name, IOS_FILE);
    if (!c.modResults.hasFile(filepath)) {
      IOSConfig.XcodeUtils.addResourceFileToGroup({ filepath, groupName: name, project: c.modResults, isBuildFile: true });
    }
    return c;
  });

module.exports = (config) => withIosKeyword(withAndroidKeyword(withMicService(config)));
