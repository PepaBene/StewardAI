import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Gallery } from "./gallery";

export const metadata: Metadata = { title: "Component gallery" };

/** Hidden in production unless NEXT_PUBLIC_DEV_GALLERY=1 (plan R1a.6). */
export default function GalleryPage() {
  if (process.env.NODE_ENV === "production" && process.env.NEXT_PUBLIC_DEV_GALLERY !== "1") notFound();
  return <Gallery />;
}
