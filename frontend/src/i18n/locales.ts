export const locales = ['de', 'en', 'fr', 'it'] as const;
export type AppLocale = (typeof locales)[number];
