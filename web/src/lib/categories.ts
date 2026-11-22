/** Brand categories from the catalog in the venue's language (ADR 0008 D6). One map for every page. */
const CATEGORY: Readonly<Record<string, string>> = {
    alcohol: "Cerveza",
    burger: "Hamburguesas",
    candy: "Dulces",
    coffee: "Café",
    cola: "Bebida",
    dairy: "Lácteos",
    energy_drink: "Bebida energética",
    food: "Comida",
    ice_cream: "Helados",
    juice: "Jugos",
    pastry: "Panadería",
    snacks: "Snacks",
    sports_drink: "Bebida isotónica",
    sports_goods: "Bebida energética",
    water: "Agua",
};

/** "sports_goods" → "Bebida energética"; an unknown key reads as words, never as a code. */
export function categoryLabel(key: string): string {
    const known = CATEGORY[key];
    if (known) return known;
    const words = key.replaceAll("_", " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
}
