import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Layering (enforced below): app -> features -> shared -> lib -> contract.
// A feature exposes its public API through its index.ts; nobody reaches into
// another feature's internals, and shared/lib never import a feature.
const featureInternals = {
    group: ["@/features/*/*"],
    message: "Import a feature through its public API (@/features/<name>), not its internals.",
};

export default tseslint.config(
    { ignores: ["dist", "coverage", "src/contract/messages.gen.ts"] },
    js.configs.recommended,
    ...tseslint.configs.strictTypeChecked,
    {
        files: ["**/*.{ts,tsx}"],
        languageOptions: {
            globals: globals.browser,
            parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
        },
        plugins: { "react-hooks": reactHooks },
        rules: {
            ...reactHooks.configs.recommended.rules,
            "@typescript-eslint/no-floating-promises": "error",
            "@typescript-eslint/no-misused-promises": ["error", { checksVoidReturn: false }],
            "@typescript-eslint/switch-exhaustiveness-check": [
                "error",
                { considerDefaultExhaustiveForUnions: true },
            ],
            "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
            "@typescript-eslint/no-unused-vars": [
                "error",
                { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
            ],
            "@typescript-eslint/consistent-type-imports": "error",
            "no-console": ["error", { allow: ["warn", "error"] }],
            "no-restricted-imports": ["error", { patterns: [featureInternals] }],
        },
    },
    {
        files: ["src/features/**/*.{ts,tsx}"],
        rules: {
            // Inside a feature, relative imports are the norm; cross-feature goes via the barrel.
            "no-restricted-imports": ["error", { patterns: [featureInternals] }],
        },
    },
    {
        files: ["src/shared/**/*.{ts,tsx}", "src/lib/**/*.{ts,tsx}", "src/contract/**/*.ts"],
        rules: {
            "no-restricted-imports": [
                "error",
                {
                    patterns: [
                        {
                            group: ["@/features/*", "@/app/*"],
                            message: "shared/lib/contract never import features or app.",
                        },
                    ],
                },
            ],
        },
    },
    {
        files: ["**/*.test.{ts,tsx}"],
        rules: { "@typescript-eslint/no-non-null-assertion": "off" },
    },
    {
        files: ["**/*.js"],
        ...tseslint.configs.disableTypeChecked,
    },
    {
        files: ["*.config.{js,ts}"],
        languageOptions: { globals: globals.node },
    },
);
