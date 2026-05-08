import BNB from "@images/bnb.svg";
import CAN from "@images/can.svg";
import ETH from "@images/eth.svg";
import LIMOCOIN32 from "@images/limo-coin32.svg";
import POL from "@images/pol.svg";
import { limoAbi, limoAddress } from "@libs/limo-contract";
import { bnbAbi, bnbAddress } from "@libs/wbnb-contract";

export const COINS = [
  {
    name: "MIR",
    address: limoAddress,
    image: LIMOCOIN32,
    abi: limoAbi,
    price: 0.00095,
  },
  {
    name: "BNB",
    address: bnbAddress,
    image: BNB,
    abi: bnbAbi,
    price: 251.1,
  },
  {
    name: "ETH",
    address: "0x2cA6DD5E1b1Ce4C4A51F3254eeEdCa931C4E2026",
    image: ETH,
    abi: limoAbi,
    price: 0.003793,
  },
  {
    name: "CAN",
    address: "0x2cA6DD5E1b1Ce4C4A51F3254eeEdCa931C4E2025",
    image: CAN,
    abi: limoAbi,
    price: 0.003793,
  },
  {
    name: "POL",
    address: "0x2cA6DD5E1b1Ce4C4A51F3254eeEdCa931C4E2024",
    image: POL,
    abi: limoAbi,
    price: 0.003793,
  },
];
