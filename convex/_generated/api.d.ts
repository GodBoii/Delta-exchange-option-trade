/* eslint-disable */
/**
 * Generated `api` utility.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import type * as applicationValidators from "../applicationValidators.js";
import type * as exchangeFills from "../exchangeFills.js";
import type * as fillValidators from "../fillValidators.js";
import type * as library from "../library.js";
import type * as orderIntents from "../orderIntents.js";
import type * as orderValidators from "../orderValidators.js";
import type * as signals from "../signals.js";
import type * as tradingAuth from "../tradingAuth.js";

import type {
  ApiFromModules,
  FilterApi,
  FunctionReference,
} from "convex/server";

declare const fullApi: ApiFromModules<{
  applicationValidators: typeof applicationValidators;
  exchangeFills: typeof exchangeFills;
  fillValidators: typeof fillValidators;
  library: typeof library;
  orderIntents: typeof orderIntents;
  orderValidators: typeof orderValidators;
  signals: typeof signals;
  tradingAuth: typeof tradingAuth;
}>;

/**
 * A utility for referencing Convex functions in your app's public API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = api.myModule.myFunction;
 * ```
 */
export declare const api: FilterApi<
  typeof fullApi,
  FunctionReference<any, "public">
>;

/**
 * A utility for referencing Convex functions in your app's internal API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = internal.myModule.myFunction;
 * ```
 */
export declare const internal: FilterApi<
  typeof fullApi,
  FunctionReference<any, "internal">
>;

export declare const components: {};
