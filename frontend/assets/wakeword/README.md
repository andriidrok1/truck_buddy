Put the custom "Hey Buddy" keyword files here (from https://console.picovoice.ai):

- hey_buddy_android.ppn
- hey_buddy_ios.ppn

They are copied into the native projects at build time by plugins/withWakeWord.js.
Until they exist, the app falls back to the built-in keyword "Jarvis".
