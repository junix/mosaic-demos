/// <reference types="vite/client" />

declare global {
  interface Window {
    __mosaicDemo?: {
      ready: boolean;
      scene: string;
      rows: number;
      interactions: number;
      error?: string;
      rankings?: Array<{rank: number; cohort: number; mean: number; q10: number; q90: number; n: number}>;
    };
  }
}

export {};
