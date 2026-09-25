import type { Metadata } from "next";

import { ProfilesEditor } from "./profiles-editor";

export const metadata: Metadata = { title: "Holding profiles" };

export default function HoldingProfilesPage() {
  return <ProfilesEditor />;
}
