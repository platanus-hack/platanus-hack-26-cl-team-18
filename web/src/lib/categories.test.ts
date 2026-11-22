import { expect, it } from "vitest";
import { categoryLabel } from "./categories";

it("names every catalog category in Spanish and never shows a raw code", () => {
    expect(categoryLabel("pastry")).toBe("Panadería");
    expect(categoryLabel("sports_goods")).toBe("Bebida energética");
    expect(categoryLabel("sports_drink")).toBe("Bebida isotónica");
    expect(categoryLabel("ice_cream")).toBe("Helados");
    expect(categoryLabel("new_thing")).toBe("New thing");
});
