import { FiChevronRight } from "react-icons/fi";

const PairCard = ({ pair }) => {
  const { stakeCoin, rewardCoin } = pair;
  return (
    <div>
      <div className="currency-section">
        <div className="currency-logo-section">
          <img src={stakeCoin?.image} alt="limo coin" width={64} />
          <p> {stakeCoin?.name}</p>
        </div>
        <span style={{ padding: "20px" }}>
          <FiChevronRight style={{ fontSize: "20px" }} />
        </span>
        <div className="currency-logo-section ">
          <img src={rewardCoin?.image} alt="limo coin" width={64} />
          <p> {rewardCoin?.name}</p>
        </div>
      </div>
    </div>
  );
};

export default PairCard;
