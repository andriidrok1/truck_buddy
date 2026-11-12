import { Alert, Platform } from "react-native";

import { t } from "../i18n";

/** A destructive action asks first: a native alert, or the browser's confirm on web (which shows `message` as is). */
export const confirmDestructive = (title: string, message: string, action: string, onYes: () => void) => {
  if (Platform.OS === "web") {
    if (window.confirm(message)) onYes();
    return;
  }
  Alert.alert(t(title), t(message), [
    { text: t("Cancel"), style: "cancel" },
    { text: t(action), style: "destructive", onPress: onYes },
  ]);
};
