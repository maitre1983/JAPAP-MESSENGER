const StakingCardStatic = ({
  title,
  APY,
  amount,
  limoLimit,
  interest,
  btnStake,
  btnUnstake,
}) => {
  return (
    <>
      <h2> {title}</h2>
      <h2 className="bold"> {APY}</h2>
      <h2 className="bold"> {amount}</h2>
      <p className="min-limo"> {limoLimit}</p>
      <div className="early-withdraw"></div>
      {/* <Button className='stake-button'>{btnStake}</Button>
      <Button className='stake-button'> {btnUnstake}</Button> */}
      <div className="early-withdraw">
        <p className="light"> {interest}</p>
      </div>
    </>
  );
};

export default StakingCardStatic;
