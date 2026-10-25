import MaterialDesignIcons from "@react-native-vector-icons/material-design-icons";
import type { ComponentProps } from "react";

export type IconName = ComponentProps<typeof MaterialDesignIcons>["name"];

export function Icon(props: { name: string; size?: number; color: string; style?: ComponentProps<typeof MaterialDesignIcons>["style"] }) {
  return <MaterialDesignIcons {...props} name={props.name as IconName} size={props.size ?? 22} />;
}
