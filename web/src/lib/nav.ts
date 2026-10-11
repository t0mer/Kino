import {
  Activity,
  Camera,
  FlaskConical,
  Images,
  Inbox,
  type LucideIcon,
  MessagesSquare,
  ScanFace,
  Settings,
  Users,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** One line shown on the page until it is built. */
  blurb: string;
}

export const NAV: NavItem[] = [
  { to: "/review", label: "Review", icon: Inbox, blurb: "Confirm or reject uncertain matches, one at a time or in bulk." },
  { to: "/kids", label: "Kids", icon: Users, blurb: "Your kids, their reference faces, coverage and auto-save." },
  { to: "/gallery", label: "Gallery", icon: Images, blurb: "Manage each kid's reference faces and spot outliers." },
  { to: "/groups", label: "Groups", icon: MessagesSquare, blurb: "Choose which WhatsApp groups Kino watches, and backfill history." },
  { to: "/photos", label: "Photos", icon: Camera, blurb: "Browse the photos saved for each kid." },
  { to: "/activity", label: "Activity", icon: Activity, blurb: "Recent images, pipeline outcomes and failed forwards." },
  { to: "/test", label: "Test", icon: FlaskConical, blurb: "Dry-run thresholds against recent faces before enabling auto-save." },
  { to: "/clusters", label: "Who is this?", icon: ScanFace, blurb: "Label clusters of unknown faces to teach Kino quickly." },
  { to: "/settings", label: "Settings", icon: Settings, blurb: "Forwarding, digest, thresholds, retention and model info." },
];
