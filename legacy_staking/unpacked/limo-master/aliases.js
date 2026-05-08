const aliases = (prefix = `src`) => ({
  "@core": `${prefix}/core`,
  "@store": `${prefix}/store`,
  "@config": `${prefix}/config`,
  "@libs": `${prefix}/libs`,
  "@images": `${prefix}/images`,
});

module.exports = aliases;
