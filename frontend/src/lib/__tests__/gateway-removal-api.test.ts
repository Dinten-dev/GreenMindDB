import { apiRemoveGateway, apiDeleteGateway, ApiError } from '../api';
const previous = global.fetch;
afterEach(() => {
  global.fetch = previous;
});
it.each([apiRemoveGateway, apiDeleteGateway])(
  'never falls back to destructive DELETE on an old server',
  async (remove) => {
    global.fetch = jest.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Not Found' }),
    })) as jest.Mock;
    await expect(remove('synthetic')).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/gateways/synthetic/remove',
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    );
  }
);
