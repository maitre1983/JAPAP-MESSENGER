import "firebase/compat/auth";
// import 'firebase/firestore' // <- needed if using firestore
// import 'firebase/functions' // <- needed if using httpsCallable
import { compose, createStore } from "redux";
import rootReducer from "./rootReducer";

export default function configureStore() {
  const createStoreWithMiddleware = compose(
    typeof window === "object" &&
      typeof window.devToolsExtension !== "undefined"
      ? () => window.__REDUX_DEVTOOLS_EXTENSION__
      : (f) => f
  )(createStore);

  const store = createStoreWithMiddleware(rootReducer);

  if (module.hot) {
    module.hot.accept("./rootReducer", () => {
      const nextRootReducer = require("./rootReducer");
      store.replaceReducer(nextRootReducer);
    });
  }

  return store;
}
