// react-native's Text that shows its string children in the driver's language (src/i18n/index.ts). Screens import
// this instead of RN's Text, so every literal label and every English line built in code ("In 500 feet, turn
// left") is translated where it is drawn, and nothing changes for text with no entry (place names, server replies
// already in Russian). Children that are elements are left alone.

import { forwardRef, type ElementRef } from "react";
import { Text as RNText, type TextProps } from "react-native";

import { t, useLang } from ".";

function translateChildren(children: TextProps["children"], lang: ReturnType<typeof useLang>): TextProps["children"] {
  if (lang === "en") return children;
  if (typeof children === "string") return t(children, undefined, lang);
  if (Array.isArray(children) && children.every((c) => typeof c === "string" || typeof c === "number")) {
    return t(children.join(""), undefined, lang);
  }
  return children;
}

export const Text = forwardRef<ElementRef<typeof RNText>, TextProps>(function Text(props, ref) {
  const lang = useLang();
  return (
    <RNText ref={ref} {...props}>
      {translateChildren(props.children, lang)}
    </RNText>
  );
});
