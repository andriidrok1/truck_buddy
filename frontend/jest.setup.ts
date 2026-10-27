// src/api.ts throws at import when the backend URL is missing; tests never hit the network,
// so any syntactically valid origin will do. ".invalid" is reserved (RFC 2606) and cannot resolve.
process.env.EXPO_PUBLIC_BACKEND_URL = "https://ci.invalid";
