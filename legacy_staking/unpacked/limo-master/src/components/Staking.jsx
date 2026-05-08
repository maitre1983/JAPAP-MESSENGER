import { limoAddress } from "@libs/limo-contract";
import { useStakeHolderStore } from "@store/StakerStore";
import { useEffect } from "react";
import { Container } from "react-bootstrap";
import { ToastContainer } from "react-toastify";
import { useAccount } from "wagmi";
import Connection from "./Connection";
import SelectCoins from "./SelectCoins";
import SelectCoinsStatic from "./SelectCoinsStatic";
import "./Staking.css";
import StakingBalance from "./StakingBalance";
import TextScroll from "./TextScroll";
import TopStakers from "./TopStakers";

const Staking = () => {
  const { isConnected, address } = useAccount();
  const stakerStore = useStakeHolderStore();

  useEffect(() => {
    const fetchCryptoPrices = async () => {
      const apiUrl =
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest";
      const apiKey = "f3215d95-fb26-4c94-a360-79074c80d5e8"; // Replace with your actual API key
      const symbols = "BTC,ETH,XRP"; // Add the symbols of the cryptocurrencies you're interested in, separated by commas

      try {
        const response = await fetch(
          `${apiUrl}?symbol=${symbols}&convert=USD`,
          {
            headers: {
              Accepts: "application/json",
              "X-CMC_PRO_API_KEY": apiKey,
            },
          }
        );
        const data = await response.json();
        console.log("data", data, response);
        // setCryptoData(data.data);
      } catch (error) {
        console.error("Error fetching crypto prices:", error);
      }
    };

    // fetchCryptoPrices();
  }, []);

  useEffect(() => {
    stakerStore.fetchStakers(limoAddress);
  }, []);

  return (
    <Container className="staking-div-container ">
      <Connection />
      <StakingBalance />
      <TextScroll />
      {isConnected && (
        <>
          <SelectCoins />
        </>
      )}
      {!isConnected && (
        <>
          <SelectCoinsStatic />
        </>
      )}

      <TopStakers />
      <ToastContainer />
    </Container>
  );
};

export default Staking;
