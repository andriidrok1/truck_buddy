import { useCallback, useRef, useState } from "react";
import { ActivityIndicator, Alert, FlatList, Platform, RefreshControl, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { api, type MemoryTurn } from "@/src/api";
import { Icon } from "@/src/components/Icon";
import { PressableScale } from "@/src/components/PressableScale";
import { fonts, radius, spacing, useTheme } from "@/src/theme";

const confirm = (msg: string, onYes: () => void) => {
  if (Platform.OS === "web") {
    if (window.confirm(msg)) onYes();
    return;
  }
  Alert.alert("Clear memory?", msg, [
    { text: "Cancel", style: "cancel" },
    { text: "Clear", style: "destructive", onPress: onYes },
  ]);
};

/** Everything the copilot remembers from past conversations, with a "clear" button. */
export default function HistoryScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const [turns, setTurns] = useState<MemoryTurn[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const listRef = useRef<FlatList<MemoryTurn>>(null);

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

  const clear = () =>
    confirm("Your copilot will forget everything you've talked about.", async () => {
      try {
        await api.clearMemory();
        setTurns([]);
      } catch {
        setError(true);
      }
    });

  return (
    <View style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top }]}>
      <View style={styles.header}>
        <PressableScale testID="history-back" accessibilityLabel="Back" onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={[styles.circle, { backgroundColor: colors.surfaceSecondary }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Conversation memory</Text>
        <View style={styles.circle} />
      </View>

      {turns === null ? (
        <ActivityIndicator style={styles.center} color={colors.muted} />
      ) : (
        <FlatList
          ref={listRef}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: false })}
          testID="history-list"
          data={turns}
          keyExtractor={(t, i) => `${t.at}-${i}`}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={async () => { setRefreshing(true); await load(); setRefreshing(false); }} />}
          contentContainerStyle={[styles.list, { paddingBottom: insets.bottom + 120 }]}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Icon name="chat-outline" size={40} color={colors.muted} />
              <Text style={[styles.emptyText, { color: colors.onSurfaceSecondary }]}>
                {error ? "Couldn't load your conversations. Pull to retry." : "Nothing yet. Talk to your copilot and it will remember."}
              </Text>
            </View>
          }
          renderItem={({ item }) => {
            const me = item.role === "driver";
            return (
              <View style={[styles.bubble, me ? styles.mine : styles.theirs, { backgroundColor: me ? colors.brandPrimary : colors.surfaceSecondary }]}>
                <Text style={[styles.text, { color: me ? colors.onBrandPrimary : colors.onSurface }]}>{item.text}</Text>
                <Text style={[styles.time, { color: me ? colors.onBrandPrimary : colors.muted }]}>
                  {new Date(item.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                </Text>
              </View>
            );
          }}
        />
      )}

      {!!turns?.length && (
        <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg, backgroundColor: colors.surface }]}>
          <PressableScale testID="history-clear" accessibilityLabel="Clear memory" onPress={clear} style={[styles.clear, { borderColor: colors.error }]}>
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
  circle: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 18 },
  center: { marginTop: spacing.xxxl },
  list: { paddingHorizontal: spacing.lg, paddingTop: spacing.md, gap: spacing.sm, flexGrow: 1 },
  bubble: { maxWidth: "82%", borderRadius: radius.lg, paddingHorizontal: spacing.lg, paddingVertical: spacing.md, gap: 4 },
  mine: { alignSelf: "flex-end", borderBottomRightRadius: radius.sm },
  theirs: { alignSelf: "flex-start", borderBottomLeftRadius: radius.sm },
  text: { fontFamily: fonts.regular, fontSize: 16, lineHeight: 22 },
  time: { fontFamily: fonts.regular, fontSize: 11, opacity: 0.7 },
  empty: { flex: 1, alignItems: "center", justifyContent: "center", gap: spacing.md, paddingHorizontal: spacing.xxl, paddingTop: spacing.xxxl },
  emptyText: { fontFamily: fonts.medium, fontSize: 15, textAlign: "center" },
  footer: { position: "absolute", left: 0, right: 0, bottom: 0, paddingHorizontal: spacing.lg, paddingTop: spacing.md },
  clear: { height: 52, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  clearText: { fontFamily: fonts.semibold, fontSize: 16 },
});
