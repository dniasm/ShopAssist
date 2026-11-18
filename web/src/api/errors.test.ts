import { describe, expect, it } from "vitest";
import { ApiError, describeError, errorFromResponse } from "./errors";

describe("errorFromResponse", () => {
  it("parses the {error:{code,message,details}} envelope", () => {
    const err = errorFromResponse(409, { error: { code: "out_of_stock", message: "Only 2 left", details: { sku_id: "SKU-1" } } });
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code, err.message, err.details]).toEqual([409, "out_of_stock", "Only 2 left", { sku_id: "SKU-1" }]);
  });
  it("flags 401 and 404 for callers", () => {
    expect(errorFromResponse(401, { error: { code: "unauthenticated", message: "x" } }).isUnauthenticated).toBe(true);
    expect(errorFromResponse(404, { error: { code: "not_found", message: "x" } }).isNotFound).toBe(true);
  });
  it("turns an unknown code into 'unknown' but keeps the message", () => {
    const err = errorFromResponse(418, { error: { code: "teapot", message: "short and stout" } });
    expect([err.code, err.message]).toEqual(["unknown", "short and stout"]);
  });
  it("copes with bodies that are not envelopes", () => {
    expect(errorFromResponse(400, "oops").code).toBe("unknown");
    expect(errorFromResponse(422, { detail: "x" }).message).toBe("Request failed (422)");
    expect(errorFromResponse(502, undefined).code).toBe("unavailable");
  });
  it("describes errors for people, naming the unavailable service", () => {
    expect(describeError(new ApiError(0, "network", "x"))).toMatch(/Can't reach/);
    expect(describeError(new ApiError(503, "unavailable", "x", { service: "CatalogService" }))).toContain("CatalogService");
    expect(describeError("weird")).toBe("Something went wrong.");
  });
});
