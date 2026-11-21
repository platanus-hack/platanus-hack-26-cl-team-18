import { useSyncExternalStore } from "react";

/**
 * A tiny external store for realtime state fed by a socket: a pure reducer
 * owns the state, React subscribes with useSyncExternalStore. Socket data
 * never goes through component state or effects.
 */
export interface Store<S, E> {
    getState: () => S;
    dispatch: (event: E) => void;
    subscribe: (listener: () => void) => () => void;
}

export function createStore<S, E>(reducer: (state: S, event: E) => S, initial: S): Store<S, E> {
    let state = initial;
    const listeners = new Set<() => void>();
    return {
        getState: () => state,
        dispatch: (event) => {
            const next = reducer(state, event);
            if (next === state) return;
            state = next;
            for (const listener of listeners) listener();
        },
        subscribe: (listener) => {
            listeners.add(listener);
            return () => listeners.delete(listener);
        },
    };
}

export function useStore<S, E, T>(store: Store<S, E>, select: (state: S) => T): T {
    return useSyncExternalStore(store.subscribe, () => select(store.getState()));
}
