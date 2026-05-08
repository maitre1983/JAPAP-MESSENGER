const PriceCard = ({ title, balance, image }) => {
  return (
    <>
      <div>
        <img src={image} alt="limo" height={32} width={32} />
      </div>
      <div>
        <h3>{title}</h3>
        <h2> {balance}</h2>
      </div>
    </>
  );
};

export default PriceCard;
