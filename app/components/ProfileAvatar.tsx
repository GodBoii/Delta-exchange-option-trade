"use client";

import Image from "next/image";
import { useState } from "react";
import { resolveProfileAvatar } from "@/lib/profile-avatar";

export function ProfileAvatar({ name, avatarUrl, large = false }: {
  name: string;
  avatarUrl?: string | null;
  large?: boolean;
}) {
  const src = resolveProfileAvatar(avatarUrl);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const initials = name.trim().slice(0, 2).toUpperCase() || "TC";

  return (
    <span className={`avatar${large ? " avatar-xl" : ""}`} aria-hidden="true">
      {src && src !== failedSrc ? (
        <Image
          key={src}
          src={src}
          alt=""
          width={large ? 64 : 30}
          height={large ? 64 : 30}
          unoptimized
          referrerPolicy="no-referrer"
          onError={() => setFailedSrc(src)}
        />
      ) : initials}
    </span>
  );
}
