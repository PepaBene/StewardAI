export declare const TOKEN_FILE: string;
export type ColorViolation = { line: number; column: number; rule: string; match: string };
export declare function findColorViolations(text: string): ColorViolation[];
export declare function scanSource(srcDir?: string): Array<ColorViolation & { file: string }>;
