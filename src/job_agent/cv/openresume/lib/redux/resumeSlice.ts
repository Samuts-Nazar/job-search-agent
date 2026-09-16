import type { FeaturedSkill } from "lib/redux/types";

// Trimmed from the original resumeSlice.ts (which pulls in @reduxjs/toolkit
// for the whole app's Redux store) to just the one constant the parser
// actually needs, to avoid an ESM/CJS interop dependency for a headless run.
export const initialFeaturedSkill: FeaturedSkill = { skill: "", rating: 4 };
export const initialFeaturedSkills: FeaturedSkill[] = Array(6).fill({
  ...initialFeaturedSkill,
});
