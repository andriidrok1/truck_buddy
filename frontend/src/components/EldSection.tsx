// The Motive API key on the truck profile screen: status, the key field, Save and Remove.
import { useState } from "react";
import { TextInput, View } from "react-native";
import { Text } from "../i18n/Text";

import { setEldToken, useEldToken } from "../eld";
import { t } from "../i18n";
import { useTheme } from "../theme";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { styles } from "./truckProfileStyles";

export function EldSection() {
  const { colors } = useTheme();
  const eldToken = useEldToken();
  const [eldDraft, setEldDraft] = useState("");
  const eldConnected = !!eldToken;
  return (
    <>
      <Text style={[styles.section, { color: colors.onSurface }]}>Hours of service (Motive)</Text>
      <View testID="eld-section" style={[styles.eldBox, { backgroundColor: colors.surfaceSecondary }]}>
        <View style={styles.eldStatusRow}>
          <Icon name={eldConnected ? "check-circle" : "link-variant-off"} size={18} color={eldConnected ? colors.success : colors.muted} />
          <Text testID="eld-status" style={[styles.switchTitle, { color: colors.onSurface }]}>
            {eldConnected ? "Connected" : "Not connected"}
          </Text>
        </View>
        <View style={[styles.inputRow, { backgroundColor: colors.surface, borderColor: colors.surface }]}>
          <TextInput
            testID="eld-key-input"
            value={eldDraft}
            onChangeText={setEldDraft}
            placeholder={t(eldConnected ? "Paste a new API key to replace" : "Motive API key")}
            placeholderTextColor={colors.muted}
            secureTextEntry
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="off"
            textContentType="none"
            returnKeyType="done"
            style={[styles.input, { color: colors.onSurface }]}
          />
        </View>
        <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
          The key comes from the Motive Fleet Dashboard: Admin → API keys → create a key, then paste it here.
        </Text>
        <View style={styles.row}>
          <PressableScale
            testID="eld-save-button"
            containerStyle={styles.flex}
            disabled={!eldDraft.trim()}
            onPress={async () => {
              await setEldToken(eldDraft);
              setEldDraft("");
            }}
            style={[styles.eldBtn, { backgroundColor: eldDraft.trim() ? colors.brandPrimary : colors.surfaceTertiary }]}
          >
            <Text style={[styles.eldBtnText, { color: eldDraft.trim() ? colors.onBrandPrimary : colors.muted }]}>Save</Text>
          </PressableScale>
          {eldConnected && (
            <PressableScale
              testID="eld-remove-button"
              containerStyle={styles.flex}
              onPress={async () => {
                await setEldToken(null);
                setEldDraft("");
              }}
              style={[styles.eldBtn, { borderColor: colors.error, borderWidth: 1.5 }]}
            >
              <Text style={[styles.eldBtnText, { color: colors.error }]}>Remove</Text>
            </PressableScale>
          )}
        </View>
        <Text style={[styles.switchSub, { color: colors.onSurfaceSecondary }]}>
          Your Motive API key stays in this phone&apos;s secure storage. It is sent only with your copilot requests so the copilot can read your hours, and is never stored on TruckBuddy servers. Remove it any time.
        </Text>
      </View>
    </>
  );
}
