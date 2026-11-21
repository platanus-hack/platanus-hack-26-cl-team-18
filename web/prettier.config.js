/** @type {import("prettier").Config} */
export default {
    printWidth: 100,
    trailingComma: "all",
    plugins: ["prettier-plugin-organize-imports", "prettier-plugin-tailwindcss"],
    tailwindStylesheet: "./src/styles/global.css",
};
