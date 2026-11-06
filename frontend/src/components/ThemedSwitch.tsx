import { Platform, Switch, type SwitchProps } from "react-native";

import { useTheme } from "../theme";

/** A Switch whose off state reads in both themes: in the dark theme the stock dark thumb on a dark track was
 *  nearly invisible (UI review 2026-11-05). `onColor` is the track when on. */
export function ThemedSwitch({ onColor, ...props }: Omit<SwitchProps, "trackColor" | "thumbColor"> & { onColor?: string }) {
  const { colors } = useTheme();
  return (
    <Switch
      {...props}
      trackColor={{ true: onColor ?? colors.info, false: colors.switchTrackOff }}
      thumbColor={colors.switchThumb}
      ios_backgroundColor={colors.switchTrackOff}
      {...(Platform.OS === "web" ? { activeThumbColor: colors.switchThumb } : {})}
    />
  );
}
