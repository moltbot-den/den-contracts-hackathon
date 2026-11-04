'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

/** Re-renders the server page every `seconds` while the contract is still running, so the timeline stays live. */
export function AutoRefresh({ seconds, active }: { seconds: number; active: boolean }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => router.refresh(), seconds * 1000);
    return () => clearInterval(id);
  }, [router, seconds, active]);
  return null;
}
