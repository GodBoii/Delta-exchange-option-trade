"use client";

/**
 * Icon layer.
 *
 * One module owns the icon family so stroke weight, optical size and metaphor
 * choices are decided once instead of per surface. Phosphor is used rather than
 * a Feather-derived set: it ships a real weight axis, so a 16px control glyph
 * and a 28px panel glyph can keep the same apparent line thickness, which a
 * fixed-stroke set cannot do.
 *
 * Names are the ones the surfaces already call, so swapping the family is a
 * single-import change at each call site and no JSX moves. A few metaphors are
 * deliberately re-chosen on the way through — a wallet for capital reads as a
 * consumer payment app, so capital uses a vault; "launch" style rockets and
 * shields-for-everything are avoided.
 */

import type { ComponentType, SVGProps } from "react";
import {
  ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowsClockwise, ArrowSquareOut, Bank,
  Broadcast, CalendarBlank, CaretDown, CaretUp, ChartBar, ChartDonut,
  ChartLineUp, Check, CirclesThreePlus, ClockCounterClockwise, Copy, CurrencyDollar,
  DownloadSimple, Equalizer, Eye, FileText, Fingerprint, FloppyDisk, Folders, Gauge,
  Info, Key, Lightning, List, Lock, MoonStars, Newspaper, Path, Plus, Prohibit,
  SignOut, Skull, SlidersHorizontal, Spinner, Square, Stack, Sun, Trash, TrendUp,
  UploadSimple, User, Vault, Warning, WifiSlash, X
} from "@phosphor-icons/react";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

/* Structure and navigation */
export const Menu = List as Icon;
export const ChevronDown = CaretDown as Icon;
export const ChevronUp = CaretUp as Icon;
export const Close = X as Icon;
export { X };
export { ArrowLeft, ArrowRight, ArrowUp, ArrowDown };
export const MoreHorizontal = CirclesThreePlus as Icon;
export const Maximize2 = ArrowSquareOut as Icon;
export const LogOut = SignOut as Icon;
export const Profile = User as Icon;

/* Sections */
export const Layers3 = Stack as Icon;
export const Layers = Stack as Icon;
export const Activity = Broadcast as Icon;
export const PieChart = ChartDonut as Icon;
export const BarChart3 = ChartBar as Icon;
export { Newspaper };
export const Bot = Path as Icon;
export const Workflow = Path as Icon;

/* Money and risk */
export const WalletCards = Vault as Icon;
export const Wallet = Bank as Icon;
export const CircleDollarSign = CurrencyDollar as Icon;
export const TrendingUp = TrendUp as Icon;
export const Shield = Gauge as Icon;
export const ShieldCheck = Fingerprint as Icon;

/* Time */
export const Clock3 = ClockCounterClockwise as Icon;
export const History = ClockCounterClockwise as Icon;
export const CalendarClock = CalendarBlank as Icon;

/* Commands */
export const RefreshCw = ArrowsClockwise as Icon;
export const Save = FloppyDisk as Icon;
export const Download = DownloadSimple as Icon;
export const Upload = UploadSimple as Icon;
export const Trash2 = Trash as Icon;
export const Play = ChartLineUp as Icon;
export const Ban = Prohibit as Icon;
export const CircleStop = Square as Icon;
export const FolderOpen = Folders as Icon;
export { Copy, FileText, Plus, Check, Info, Eye };
export const SlidersHorizontal2 = SlidersHorizontal as Icon;
export { SlidersHorizontal };
export const Equalizer2 = Equalizer as Icon;

/* State */
export const AlertTriangle = Warning as Icon;
export const LoaderCircle = Spinner as Icon;
export const WifiOff = WifiSlash as Icon;
export const KeyRound = Key as Icon;
export const LockKeyhole = Lock as Icon;
export const Zap = Lightning as Icon;
export const Danger = Skull as Icon;

/* Appearance */
export const ThemeLight = Sun as Icon;
export const ThemeDark = MoonStars as Icon;
export const ThemeSystem = Gauge as Icon;
