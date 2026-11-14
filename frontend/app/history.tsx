import { useCallback, useMemo, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Platform, RefreshControl, StyleSheet, View } from "react-native";
import { Text } from "@/src/i18n/Text";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { api, type MemoryTurn } from "@/src/api";
import { clearConversations } from "@/src/state/accountActions";
import { Icon } from "@/src/components/Icon";
import { HistoryEmpty } from "@/src/components/HistoryEmpty";
import { PressableScale } from "@/src/components/PressableScale";
import { historyRows } from "@/src/utils/historyRows";
import { fonts, radius, spacing, useTheme } from "@/src/theme";
import { t, useLang, type Lang } from "@/src/i18n";
import { clockTime, shortDate } from "@/src/utils/arrivalClock";

const confirm = (msg: string, onYes: () => void) => {
  if (Platform.OS === "web") {
    if (window.confirm(msg)) onYes();
    return;
  }
  Alert.alert(t("Clear memory?"), t(msg), [
    { text: t("Cancel"), style: "cancel" },
    { text: t("Clear"), style: "destructive", onPress: onYes },
  ]);
};

/** Everything the copilot remembers from past conversations, with a "clear" button. */

const CLEAR_MSG = "Your copilot forgets your conversations and what it learned from them. Your truck and trips stay.";

/** "Today", "Yesterday", else "Oct 28": one separator per day instead of a date under every bubble. */
function dayLabel(iso: string, lang: Lang): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return shortDate(d, lang);
}

export default function HistoryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const lang = useLang(); // the bubbles' clock and the day separators follow the app's language
  const [turns, setTurns] = useState<MemoryTurn[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  // The Clear button floats over the list: the list ends above it, whatever the font size (QA U13).
  const [footerH, setFooterH] = useState(100);

  const load = useCallback(
    () =>
      api
        .memory()
        .then((r) => {
          setTurns(r.turns); // oldest first, like a chat; the list scrolls to the newest
          setError(false);
        })
        .catch(() => {
          setError(true);
          setTurns((t) => t ?? []);
        }),
    [],
  );

  // Reload every time the screen is in front, so turns saved during the current dialog
  // show up when the driver comes back from the map (and not only on first mount).
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const rows = useMemo(() => historyRows(turns ?? []), [turns]);
  const [retrying, setRetrying] = useState(false);
  const retry = async () => {
    setRetrying(true);
    await load();
    setRetrying(false);
  };

  const clear = () =>
    confirm(CLEAR_MSG, async () => {
      try {
        await clearConversations();
        setTurns([]);
        setError(false); // a refresh that failed earlier must not turn the empty state into "Couldn't load"
      } catch {
        // The list is still on screen, and the error text is only drawn for an empty one: say it here.
        const msg = t("Your data is still there. Check your connection and try again.");
        if (Platform.OS === "web") window.alert(msg);
        else Alert.alert(t("Couldn't reach the server"), msg);
      }
    });

  return (
    <View style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top }]}>
      <View style={styles.header}>
        <PressableScale testID="history-back" accessibilityLabel={t("Back")} onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={[styles.circle, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Conversation memory</Text>
        <View style={styles.circle} />
      </View>

      {turns === null ? (
        <ActivityIndicator style={styles.center} color={colors.muted} />
      ) : rows.length === 0 ? (
        // Outside the list: an inverted list flips its empty component too (mirrored on Android, review 2026-11-05).
        <HistoryEmpty error={error} retrying={retrying} onRetry={retry} />
      ) : (
        // Inverted: the newest turn sits at the bottom from the first frame. scrollToEnd on layout ran before
        // the rows were measured and the screen opened with the last two messages below the fold (QA 2026-11-02).
        <FlatList
          inverted
          testID="history-list"
          data={rows}
          keyExtractor={(r) => r.key}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} />}
          contentContainerStyle={[styles.list, { paddingTop: footerH + spacing.md }]}
          renderItem={({ item: row }) => {
            if (row.kind === "day") {
              return (
                <Text testID="history-day" style={[styles.day, { color: colors.muted }]}>
                  {dayLabel(row.at, lang)}
                </Text>
              );
            }
            const item = row.turn;
            const me = item.role === "driver";
            return (
              <View style={[styles.bubble, me ? styles.mine : styles.theirs, { backgroundColor: me ? colors.brandPrimary : colors.surfaceSecondary }]}>
                <Text style={[styles.text, { color: me ? colors.onBrandPrimary : colors.onSurface }]}>{item.text}</Text>
                <Text style={[styles.time, { color: me ? colors.onBrandPrimary : colors.muted }]}>
                  {clockTime(new Date(item.at), lang)}
                </Text>
              </View>
            );
          }}
        />
      )}

      {!!turns?.length && (
        <View
          onLayout={(e) => setFooterH(e.nativeEvent.layout.height)}
          style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg, backgroundColor: colors.surface }]}
        >
          <PressableScale testID="history-clear" accessibilityLabel={t("Clear memory")} onPress={clear} style={[styles.clear, { borderColor: colors.error }]}>
            <Icon name="delete-outline" size={20} color={colors.error} />
            <Text style={[styles.clearText, { color: colors.error }]}>Clear memory</Text>
          </PressableScale>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  circle: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 18 },
  center: { marginTop: spacing.xxxl },
  list: { paddingHorizontal: spacing.lg, paddingTop: spacing.md, gap: spacing.sm, flexGrow: 1 },
  bubble: { maxWidth: "82%", borderRadius: radius.lg, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: 4 },
  mine: { alignSelf: "flex-end", borderBottomRightRadius: radius.sm },
  theirs: { alignSelf: "flex-start", borderBottomLeftRadius: radius.sm },
  text: { fontFamily: fonts.regular, fontSize: 16, lineHeight: 22 },
  time: { fontFamily: fonts.regular, fontSize: 11, opacity: 0.7 },
  day: { fontFamily: fonts.semibold, fontSize: 12, textAlign: "center", marginTop: spacing.md, marginBottom: spacing.xs, textTransform: "uppercase", letterSpacing: 0.6 },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  clear: { height: 56, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  clearText: { fontFamily: fonts.semibold, fontSize: 16 },
});
