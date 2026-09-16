/* eslint-disable */
/**
 * Generated `api` utility.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import type * as accounts from "../accounts.js";
import type * as applicationValidators from "../applicationValidators.js";
import type * as exchangeFills from "../exchangeFills.js";
import type * as fillValidators from "../fillValidators.js";
import type * as library from "../library.js";
import type * as orderIntents from "../orderIntents.js";
import type * as orderValidators from "../orderValidators.js";
import type * as runtimeAutomation from "../runtimeAutomation.js";
import type * as runtimeControl from "../runtimeControl.js";
import type * as runtimeRecords from "../runtimeRecords.js";
import type * as runtimeTables from "../runtimeTables.js";
import type * as sharedAnalysis from "../sharedAnalysis.js";
import type * as signals from "../signals.js";
import type * as strategyDefinition from "../strategyDefinition.js";
import type * as tradingAuth from "../tradingAuth.js";

import type {
  ApiFromModules,
  FilterApi,
  FunctionReference,
} from "convex/server";

declare const fullApi: ApiFromModules<{
  accounts: typeof accounts;
  applicationValidators: typeof applicationValidators;
  exchangeFills: typeof exchangeFills;
  fillValidators: typeof fillValidators;
  library: typeof library;
  orderIntents: typeof orderIntents;
  orderValidators: typeof orderValidators;
  runtimeAutomation: typeof runtimeAutomation;
  runtimeControl: typeof runtimeControl;
  runtimeRecords: typeof runtimeRecords;
  runtimeTables: typeof runtimeTables;
  sharedAnalysis: typeof sharedAnalysis;
  signals: typeof signals;
  strategyDefinition: typeof strategyDefinition;
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
