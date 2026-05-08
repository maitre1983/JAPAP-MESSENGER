import { createWeb3Modal, defaultWagmiConfig } from "@web3modal/wagmi/react";
import { useCallback, useState } from "react";
import { Provider } from "react-redux";

// import { bsc } from "@wagmi/chains";
import { ReactReduxFirebaseProvider } from "react-redux-firebase";
import "react-toastify/dist/ReactToastify.css";
import { bsc } from "viem/chains";
import { WagmiConfig } from "wagmi";
import "./App.css";
import Staking from "./components/Staking";
import { UserStackedContext } from "./core/UserStackedContext";
import { rrfProps, store } from "./store";

const projectId = "a480c2eef21d206c772d7fc377dc5886";

const metadata = {
  name: "Web3Modal",
  description: "Web3Modal Example",
  url: "https://web3modal.com",
  icons: ["https://avatars.githubusercontent.com/u/37784886"],
};

const chains = [bsc];

const wagmiConfig = defaultWagmiConfig({ chains, projectId, metadata });

createWeb3Modal({
  wagmiConfig,
  projectId,
  chains: chains,
  defaultChain: bsc,
});

function App() {
  const [stackedItems, setStackedItems] = useState([]);

  const onStackedItems = useCallback(
    (items, poolId) => {},
    [stackedItems.length]
  );

  const value = { stackedItems, onStackedItems };

  return (
    <Provider store={store}>
      <ReactReduxFirebaseProvider {...rrfProps}>
        <UserStackedContext.Provider value={value}>
          <WagmiConfig config={wagmiConfig}>
            <Staking />
          </WagmiConfig>
        </UserStackedContext.Provider>
      </ReactReduxFirebaseProvider>
    </Provider>
  );
}

export default App;
