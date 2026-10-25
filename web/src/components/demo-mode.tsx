"use client";

import { createContext, useContext } from "react";

// demoMode() reads server-only settings; the root layout passes the answer down so client pages can adapt.
const DemoMode = createContext(false);

export function DemoModeProvider({ demo, children }: { demo: boolean; children: React.ReactNode }) {
  return <DemoMode.Provider value={demo}>{children}</DemoMode.Provider>;
}

export function useDemoMode() {
  return useContext(DemoMode);
}
