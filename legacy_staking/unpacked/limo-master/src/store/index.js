// import firebase from "firebase/app";
import firebase from "firebase/compat/app";
import "firebase/compat/firestore";

// import firebase from "firebase/compat/app";
import "firebase/compat/auth";
// import "firebase/firestore"; // <- needed if using firestore
// import 'firebase/functions' // <- needed if using httpsCallable
import {
  firebase as fbConfig,
  reduxFirebase as rfConfig,
} from "@config/firebase";
import { createFirestoreInstance } from "redux-firestore";
import configureStore from "./configureStore";

// react-redux-firebase config
const rrfConfig = rfConfig;

// Initialize firebase instance

firebase.initializeApp(fbConfig);
// const app = initializeApp(fbConfig);

// const db = getFirestore(app);

// Initialize other services on firebase instance
firebase.firestore(); // <- needed if using firestore
// firebase.functions() // <- needed if using httpsCallable

// Create store with reducers and initial state
const initialState = {};

export const db = firebase.firestore();

export const store = configureStore();

export const rrfProps = {
  firebase: firebase,
  config: rrfConfig,
  dispatch: store.dispatch,
  createFirestoreInstance, // <- needed if using firestore
};
