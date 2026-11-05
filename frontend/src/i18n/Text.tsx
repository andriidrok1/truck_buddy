// react-native's Text that shows its string children in the driver's language (src/i18n/index.ts). Screens import
// this instead of RN's Text, so every literal label and every English line built in code ("In 500 feet, turn
// left") is translated where it is drawn, and nothing changes for text with no entry (place names, server replies
// already in Russian). Children that are elements are left alone.

import { forwardRef, type ElementRef } from "react";
import { StyleSheet, Text as RNText, type TextProps } from "react-native";

import { fontFor } from "../theme";
import { t, useLang } from ".";

function translateChildren(children: TextProps["children"], lang: ReturnType<typeof useLang>): TextProps["children"] {
  if (lang === "en") return children;
  if (typeof children === "string") return t(children, undefined, lang);
  if (Array.isArray(children) && children.every((c) => typeof c === "string" || typeof c === "number")) {
    return t(children.join(""), undefined, lang);
  }
  return children;
}

/** The style with its font family swapped for one that has the language's letters (theme.ts fontFor). */
function localStyle(style: TextProps["style"], lang: ReturnType<typeof useLang>): TextProps["style"] {
  if (lang === "en" || !style) return style;
  const family = StyleSheet.flatten(style)?.fontFamily;
  const local = fontFor(family, lang);
  return local === family ? style : [style, { fontFamily: local }];
}

export const Text = forwardRef<ElementRef<typeof RNText>, TextProps>(function Text(props, ref) {
  const lang = useLang();
  return (
    <RNText ref={ref} {...props} style={localStyle(props.style, lang)}>
      {translateChildren(props.children, lang)}
    </RNText>
  );
});
